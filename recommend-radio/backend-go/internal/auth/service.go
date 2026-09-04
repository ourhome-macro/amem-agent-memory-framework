package auth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"recommend-radio/backend-go/internal/platform/model"
	"recommend-radio/backend-go/internal/platform/respond"
)

const (
	providerBili     = "bilibili"
	qrExpiresSeconds = 180
)

var defaultEndpoints = Endpoints{
	QRGenerateURL: "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
	QRPollURL:     "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
	NavURL:        "https://api.bilibili.com/x/web-interface/nav",
}

type Endpoints struct {
	QRGenerateURL string
	QRPollURL     string
	NavURL        string
}

type Service struct {
	db        *gorm.DB
	client    *http.Client
	endpoints Endpoints
}

type UserProfile struct {
	MID     int64  `json:"mid"`
	Name    string `json:"name"`
	Face    string `json:"face"`
	Level   *int   `json:"level,omitempty"`
	VIPType *int   `json:"vipType,omitempty"`
}

type Status struct {
	QRLoginEnabled  bool         `json:"qrLoginEnabled"`
	IsLoggedIn      bool         `json:"isLoggedIn"`
	User            *UserProfile `json:"user"`
	CookieUpdatedAt *time.Time   `json:"cookieUpdatedAt,omitempty"`
}

type QRCode struct {
	QRCodeKey      string    `json:"qrcodeKey"`
	URL            string    `json:"url"`
	ExpiresAt      time.Time `json:"expiresAt"`
	PollIntervalMS int       `json:"pollIntervalMs"`
}

type QRStatus struct {
	QRCodeKey  string       `json:"qrcodeKey"`
	Status     string       `json:"status"`
	Code       int          `json:"code"`
	Message    string       `json:"message"`
	IsLoggedIn bool         `json:"isLoggedIn"`
	User       *UserProfile `json:"user"`
}

type biliEnvelope struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

func NewService(db *gorm.DB, client *http.Client) *Service {
	return NewServiceWithEndpoints(db, client, defaultEndpoints)
}

func NewServiceWithEndpoints(db *gorm.DB, client *http.Client, endpoints Endpoints) *Service {
	if client == nil {
		client = &http.Client{Timeout: 10 * time.Second}
	}
	if endpoints.QRGenerateURL == "" {
		endpoints.QRGenerateURL = defaultEndpoints.QRGenerateURL
	}
	if endpoints.QRPollURL == "" {
		endpoints.QRPollURL = defaultEndpoints.QRPollURL
	}
	if endpoints.NavURL == "" {
		endpoints.NavURL = defaultEndpoints.NavURL
	}
	return &Service{db: db, client: client, endpoints: endpoints}
}

func (s *Service) CreateQRCode(ctx context.Context, userID string) (QRCode, error) {
	payload, _, err := s.getBiliPayload(ctx, s.endpoints.QRGenerateURL, nil, nil)
	if err != nil {
		return QRCode{}, err
	}
	if payload.Code != 0 {
		return QRCode{}, respond.Upstream(firstNonEmpty(payload.Message, "Bilibili QR code failed"))
	}
	var data struct {
		QRCodeKey string `json:"qrcode_key"`
		URL       string `json:"url"`
	}
	if err := json.Unmarshal(payload.Data, &data); err != nil {
		return QRCode{}, respond.Upstream("Bilibili QR code returned invalid JSON")
	}
	data.QRCodeKey = strings.TrimSpace(data.QRCodeKey)
	data.URL = strings.TrimSpace(data.URL)
	if data.QRCodeKey == "" || data.URL == "" {
		return QRCode{}, respond.Upstream("Bilibili QR code response missing qrcode_key or url")
	}
	now := time.Now()
	expiresAt := now.Add(qrExpiresSeconds * time.Second)
	session := model.AuthQRSession{
		UserID:    userID,
		QRCodeKey: data.QRCodeKey,
		URL:       data.URL,
		Status:    "waiting",
		CreatedAt: now,
		UpdatedAt: now,
		ExpiresAt: &expiresAt,
	}
	if err := s.db.WithContext(ctx).Clauses(clause.OnConflict{
		Columns: []clause.Column{{Name: "user_id"}, {Name: "qrcode_key"}},
		DoUpdates: clause.AssignmentColumns([]string{
			"url", "status", "message", "updated_at", "expires_at",
		}),
	}).Create(&session).Error; err != nil {
		return QRCode{}, err
	}
	return QRCode{
		QRCodeKey:      data.QRCodeKey,
		URL:            data.URL,
		ExpiresAt:      expiresAt,
		PollIntervalMS: 2000,
	}, nil
}

func (s *Service) PollQRCode(ctx context.Context, userID string, qrcodeKey string) (QRStatus, error) {
	qrcodeKey = strings.TrimSpace(qrcodeKey)
	if qrcodeKey == "" {
		return QRStatus{}, respond.BadRequest("qrcodeKey is required")
	}
	var session model.AuthQRSession
	err := s.db.WithContext(ctx).Where("user_id = ? AND qrcode_key = ?", userID, qrcodeKey).First(&session).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return QRStatus{}, respond.NotFound("QR code session not found")
	}
	if err != nil {
		return QRStatus{}, err
	}
	if session.ExpiresAt != nil && time.Now().After(*session.ExpiresAt) {
		_ = s.saveQRStatus(ctx, userID, qrcodeKey, "expired", "QR code expired")
		return QRStatus{QRCodeKey: qrcodeKey, Status: "expired", Code: 86038, Message: "QR code expired"}, nil
	}

	pollURL, err := withQuery(s.endpoints.QRPollURL, "qrcode_key", qrcodeKey)
	if err != nil {
		return QRStatus{}, err
	}
	payload, resp, err := s.getBiliPayload(ctx, pollURL, nil, nil)
	if err != nil {
		return QRStatus{}, err
	}
	if payload.Code != 0 {
		return QRStatus{}, respond.Upstream(firstNonEmpty(payload.Message, "Bilibili QR poll failed"))
	}
	var data map[string]any
	if err := json.Unmarshal(payload.Data, &data); err != nil {
		return QRStatus{}, respond.Upstream("Bilibili QR poll returned invalid JSON")
	}
	biliCode := intFromAny(data["code"])
	status := statusFromBiliCode(biliCode)
	message := firstNonEmpty(stringFromAny(data["message"]), payload.Message)
	var user *UserProfile
	if status == "confirmed" {
		cookieHeader := cookieHeaderFromResponse(resp)
		if cookieHeader == "" {
			return QRStatus{}, respond.Upstream("Bilibili QR poll succeeded without Set-Cookie")
		}
		profile, err := s.RefreshProfile(ctx, cookieHeader)
		if err != nil {
			return QRStatus{}, err
		}
		refreshToken := stringFromAny(data["refresh_token"])
		if err := s.saveAccount(ctx, userID, cookieHeader, refreshToken, profile); err != nil {
			return QRStatus{}, err
		}
		user = &profile
	}
	if err := s.saveQRStatus(ctx, userID, qrcodeKey, status, message); err != nil {
		return QRStatus{}, err
	}
	if user == nil {
		current, _ := s.Status(ctx, userID, false)
		user = current.User
	}
	return QRStatus{
		QRCodeKey:  qrcodeKey,
		Status:     status,
		Code:       biliCode,
		Message:    message,
		IsLoggedIn: status == "confirmed",
		User:       user,
	}, nil
}

func (s *Service) Status(ctx context.Context, userID string, refresh bool) (Status, error) {
	account, cookieHeader, err := s.accountWithCookie(ctx, userID)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return Status{QRLoginEnabled: true, IsLoggedIn: false}, nil
	}
	if err != nil {
		return Status{}, err
	}
	if cookieHeader == "" {
		return Status{QRLoginEnabled: true, IsLoggedIn: false}, nil
	}
	var user *UserProfile
	if refresh {
		profile, err := s.RefreshProfile(ctx, cookieHeader)
		if err != nil {
			return Status{}, err
		}
		if err := s.saveAccount(ctx, userID, cookieHeader, decryptPlain(account.RefreshTokenEncrypted), profile); err != nil {
			return Status{}, err
		}
		user = &profile
	} else if account.UserMID != nil {
		user = &UserProfile{
			MID:  *account.UserMID,
			Name: account.UserName,
			Face: account.UserFace,
		}
	}
	return Status{
		QRLoginEnabled:  true,
		IsLoggedIn:      true,
		User:            user,
		CookieUpdatedAt: account.CookieUpdatedAt,
	}, nil
}

func (s *Service) Profile(ctx context.Context, userID string, refresh bool) (UserProfile, error) {
	_, cookieHeader, err := s.accountWithCookie(ctx, userID)
	if errors.Is(err, gorm.ErrRecordNotFound) || cookieHeader == "" {
		return UserProfile{}, respond.APIError{Status: http.StatusUnauthorized, Code: "AUTH_REQUIRED", Message: "Bilibili login is required"}
	}
	if err != nil {
		return UserProfile{}, err
	}
	if refresh {
		profile, err := s.RefreshProfile(ctx, cookieHeader)
		if err != nil {
			return UserProfile{}, err
		}
		if err := s.saveAccount(ctx, userID, cookieHeader, "", profile); err != nil {
			return UserProfile{}, err
		}
		return profile, nil
	}
	status, err := s.Status(ctx, userID, false)
	if err != nil {
		return UserProfile{}, err
	}
	if status.User == nil {
		return UserProfile{}, respond.APIError{Status: http.StatusUnauthorized, Code: "AUTH_REQUIRED", Message: "Bilibili login is required"}
	}
	return *status.User, nil
}

func (s *Service) CookieHeader(ctx context.Context, userID string) (string, error) {
	_, cookieHeader, err := s.accountWithCookie(ctx, userID)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return "", nil
	}
	return cookieHeader, err
}

func (s *Service) Logout(ctx context.Context, userID string) (map[string]any, error) {
	result := s.db.WithContext(ctx).Where("user_id = ? AND provider = ?", userID, providerBili).Delete(&model.BiliAccount{})
	if result.Error != nil {
		return nil, result.Error
	}
	return map[string]any{"loggedOut": result.RowsAffected > 0}, nil
}

func (s *Service) RefreshProfile(ctx context.Context, cookieHeader string) (UserProfile, error) {
	payload, _, err := s.getBiliPayload(ctx, s.endpoints.NavURL, map[string]string{"Cookie": cookieHeader}, nil)
	if err != nil {
		return UserProfile{}, err
	}
	var data map[string]any
	if err := json.Unmarshal(payload.Data, &data); err != nil {
		return UserProfile{}, respond.Upstream("Bilibili nav returned invalid JSON")
	}
	if payload.Code != 0 || !boolFromAny(data["isLogin"]) {
		return UserProfile{}, respond.APIError{Status: http.StatusUnauthorized, Code: "AUTH_REQUIRED", Message: firstNonEmpty(payload.Message, "Bilibili login is required")}
	}
	return normalizeUserProfile(data), nil
}

func (s *Service) accountWithCookie(ctx context.Context, userID string) (model.BiliAccount, string, error) {
	var account model.BiliAccount
	err := s.db.WithContext(ctx).Where("user_id = ? AND provider = ?", userID, providerBili).First(&account).Error
	if err != nil {
		return model.BiliAccount{}, "", err
	}
	return account, decryptPlain(account.CookieEncrypted), nil
}

func (s *Service) saveAccount(ctx context.Context, userID string, cookieHeader string, refreshToken string, user UserProfile) error {
	now := time.Now()
	cookieValue := encryptPlain(cookieHeader)
	var refreshValue *string
	if strings.TrimSpace(refreshToken) != "" {
		refresh := encryptPlain(refreshToken)
		refreshValue = &refresh
	}
	account := model.BiliAccount{
		UserID:                userID,
		Provider:              providerBili,
		CookieEncrypted:       &cookieValue,
		RefreshTokenEncrypted: refreshValue,
		UserMID:               &user.MID,
		UserName:              user.Name,
		UserFace:              user.Face,
		CookieUpdatedAt:       &now,
		UpdatedAt:             now,
	}
	assignments := map[string]any{
		"cookie_encrypted":  account.CookieEncrypted,
		"user_mid":          account.UserMID,
		"user_name":         account.UserName,
		"user_face":         account.UserFace,
		"cookie_updated_at": account.CookieUpdatedAt,
		"updated_at":        account.UpdatedAt,
	}
	if refreshValue != nil {
		assignments["refresh_token_encrypted"] = refreshValue
	}
	return s.db.WithContext(ctx).Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "user_id"}, {Name: "provider"}},
		DoUpdates: clause.Assignments(assignments),
	}).Create(&account).Error
}

func (s *Service) saveQRStatus(ctx context.Context, userID string, qrcodeKey string, status string, message string) error {
	now := time.Now()
	var msg *string
	if strings.TrimSpace(message) != "" {
		msg = &message
	}
	return s.db.WithContext(ctx).Model(&model.AuthQRSession{}).
		Where("user_id = ? AND qrcode_key = ?", userID, qrcodeKey).
		Updates(map[string]any{"status": status, "message": msg, "updated_at": now}).Error
}

func (s *Service) getBiliPayload(ctx context.Context, target string, headers map[string]string, body io.Reader) (biliEnvelope, *http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, body)
	if err != nil {
		return biliEnvelope{}, nil, err
	}
	for key, value := range defaultHeaders() {
		req.Header.Set(key, value)
	}
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	resp, err := s.client.Do(req)
	if err != nil {
		if errors.Is(ctx.Err(), context.Canceled) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return biliEnvelope{}, nil, ctx.Err()
		}
		return biliEnvelope{}, nil, respond.Upstream(err.Error())
	}
	if resp.StatusCode >= 400 {
		defer resp.Body.Close()
		return biliEnvelope{}, resp, respond.Upstream(fmt.Sprintf("Bilibili HTTP %d", resp.StatusCode))
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	defer resp.Body.Close()
	if err != nil {
		return biliEnvelope{}, resp, err
	}
	var payload biliEnvelope
	if err := json.Unmarshal(raw, &payload); err != nil {
		return biliEnvelope{}, resp, respond.Upstream("Bilibili returned non-JSON response")
	}
	return payload, resp, nil
}

func defaultHeaders() map[string]string {
	return map[string]string{
		"Accept":     "application/json, text/plain, */*",
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
		"Referer":    "https://www.bilibili.com/",
	}
}

func statusFromBiliCode(code int) string {
	switch code {
	case 0:
		return "confirmed"
	case 86038:
		return "expired"
	case 86090:
		return "scanned"
	case 86101:
		return "waiting"
	default:
		return "unknown"
	}
}

func cookieHeaderFromResponse(resp *http.Response) string {
	if resp == nil {
		return ""
	}
	cookies := resp.Cookies()
	if len(cookies) == 0 {
		return ""
	}
	seen := map[string]string{}
	order := make([]string, 0, len(cookies))
	for _, cookie := range cookies {
		name := strings.TrimSpace(cookie.Name)
		if name == "" {
			continue
		}
		if _, ok := seen[name]; !ok {
			order = append(order, name)
		}
		seen[name] = cookie.Value
	}
	parts := make([]string, 0, len(order))
	for _, name := range order {
		parts = append(parts, name+"="+seen[name])
	}
	return strings.Join(parts, "; ")
}

func normalizeUserProfile(data map[string]any) UserProfile {
	levelInfo, _ := data["level_info"].(map[string]any)
	vipInfo, _ := data["vip"].(map[string]any)
	level := intFromAny(levelInfo["current_level"])
	vipType := intFromAny(vipInfo["type"])
	return UserProfile{
		MID:     int64FromAny(data["mid"]),
		Name:    stringFromAny(data["uname"]),
		Face:    stringFromAny(data["face"]),
		Level:   intPtrIfNonZero(level),
		VIPType: intPtrIfNonZero(vipType),
	}
}

func withQuery(rawURL string, key string, value string) (string, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return "", err
	}
	q := parsed.Query()
	q.Set(key, value)
	parsed.RawQuery = q.Encode()
	return parsed.String(), nil
}

func encryptPlain(value string) string {
	return "plain:" + value
}

func decryptPlain(value *string) string {
	if value == nil {
		return ""
	}
	raw := strings.TrimSpace(*value)
	if strings.HasPrefix(raw, "plain:") {
		return strings.TrimPrefix(raw, "plain:")
	}
	return raw
}

func intPtrIfNonZero(value int) *int {
	if value == 0 {
		return nil
	}
	return &value
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

func stringFromAny(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case json.Number:
		return v.String()
	case float64:
		return strconv.FormatFloat(v, 'f', -1, 64)
	case int:
		return strconv.Itoa(v)
	case int64:
		return strconv.FormatInt(v, 10)
	default:
		return ""
	}
}

func intFromAny(value any) int {
	return int(int64FromAny(value))
}

func int64FromAny(value any) int64 {
	switch v := value.(type) {
	case float64:
		return int64(v)
	case int:
		return int64(v)
	case int64:
		return v
	case json.Number:
		n, _ := v.Int64()
		return n
	case string:
		n, _ := strconv.ParseInt(v, 10, 64)
		return n
	default:
		return 0
	}
}

func boolFromAny(value any) bool {
	switch v := value.(type) {
	case bool:
		return v
	case string:
		return strings.EqualFold(v, "true") || v == "1"
	case float64:
		return v != 0
	default:
		return false
	}
}
