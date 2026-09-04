package respond

import (
	"errors"
	"net/http"

	"github.com/gin-gonic/gin"
)

type Envelope struct {
	Success bool        `json:"success"`
	Data    any         `json:"data,omitempty"`
	Error   *ErrorShape `json:"error,omitempty"`
}

type ErrorShape struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type APIError struct {
	Status  int
	Code    string
	Message string
}

func (e APIError) Error() string {
	return e.Message
}

func OK(c *gin.Context, data any) {
	c.JSON(http.StatusOK, Envelope{Success: true, Data: data})
}

func Accepted(c *gin.Context, data any) {
	c.JSON(http.StatusAccepted, Envelope{Success: true, Data: data})
}

func NoContent(c *gin.Context) {
	c.JSON(http.StatusOK, Envelope{Success: true})
}

func Error(c *gin.Context, err error) {
	var apiErr APIError
	if errors.As(err, &apiErr) {
		c.JSON(apiErr.Status, Envelope{
			Success: false,
			Error:   &ErrorShape{Code: apiErr.Code, Message: apiErr.Message},
		})
		return
	}
	c.JSON(http.StatusInternalServerError, Envelope{
		Success: false,
		Error:   &ErrorShape{Code: "UNKNOWN_ERROR", Message: err.Error()},
	})
}

func BadRequest(message string) APIError {
	return APIError{Status: http.StatusBadRequest, Code: "VALIDATION_ERROR", Message: message}
}

func NotFound(message string) APIError {
	return APIError{Status: http.StatusNotFound, Code: "NOT_FOUND", Message: message}
}

func Upstream(message string) APIError {
	return APIError{Status: http.StatusBadGateway, Code: "API_ERROR", Message: message}
}

func TooManyRequests(message string) APIError {
	return APIError{Status: http.StatusTooManyRequests, Code: "TOO_MANY_REQUESTS", Message: message}
}
