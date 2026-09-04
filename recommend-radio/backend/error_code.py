from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorCode(Enum):
    SUCCESS = 0
    UNKNOWN_ERROR = -1

    INVALID_BVID = 1001
    VIDEO_NOT_FOUND = 1002
    INVALID_INPUT = 1003
    NO_AUDIO_LOADED = 1004
    VALIDATION_ERROR = 1005
    NOT_FOUND = 1006
    CONFLICT = 1007
    AUTH_REQUIRED = 1008
    FORBIDDEN = 1009

    NETWORK_ERROR = 2001
    REQUEST_TIMEOUT = 2002

    API_ERROR = 3001
    NO_DASH_STREAM = 3002
    NO_AUDIO_STREAM = 3003


class ErrorMessage:
    INVALID_BVID = "Invalid BVID format"
    VIDEO_NOT_FOUND = "Video not found"
    INVALID_INPUT = "Invalid BVID or Bilibili video URL"
    INPUT_EMPTY = "Input is required"
    NO_AUDIO_LOADED = "No audio loaded"
    VALIDATION_ERROR = "Validation error"
    NOT_FOUND = "Resource not found"
    CONFLICT = "Resource conflict"
    AUTH_REQUIRED = "Authentication required"
    FORBIDDEN = "Permission denied"

    NETWORK_ERROR = "Network request failed"
    REQUEST_TIMEOUT = "Request timeout"

    API_ERROR = "External API request failed"
    NO_DASH_STREAM = "No DASH stream available"
    NO_AUDIO_STREAM = "No audio stream available"

    PLAYBACK_FAILED = "Playback failed"


class APIError(Exception):
    def __init__(self, code: ErrorCode, message: Optional[str] = None, status_code: int = 400):
        self.code = code
        self.message = message or self._get_default_message(code)
        self.status_code = status_code
        super().__init__(self.message)

    @staticmethod
    def _get_default_message(code: ErrorCode) -> str:
        message_map = {
            ErrorCode.INVALID_BVID: ErrorMessage.INVALID_BVID,
            ErrorCode.VIDEO_NOT_FOUND: ErrorMessage.VIDEO_NOT_FOUND,
            ErrorCode.INVALID_INPUT: ErrorMessage.INVALID_INPUT,
            ErrorCode.NO_AUDIO_LOADED: ErrorMessage.NO_AUDIO_LOADED,
            ErrorCode.VALIDATION_ERROR: ErrorMessage.VALIDATION_ERROR,
            ErrorCode.NOT_FOUND: ErrorMessage.NOT_FOUND,
            ErrorCode.CONFLICT: ErrorMessage.CONFLICT,
            ErrorCode.AUTH_REQUIRED: ErrorMessage.AUTH_REQUIRED,
            ErrorCode.FORBIDDEN: ErrorMessage.FORBIDDEN,
            ErrorCode.NETWORK_ERROR: ErrorMessage.NETWORK_ERROR,
            ErrorCode.REQUEST_TIMEOUT: ErrorMessage.REQUEST_TIMEOUT,
            ErrorCode.API_ERROR: ErrorMessage.API_ERROR,
            ErrorCode.NO_DASH_STREAM: ErrorMessage.NO_DASH_STREAM,
            ErrorCode.NO_AUDIO_STREAM: ErrorMessage.NO_AUDIO_STREAM,
        }
        return message_map.get(code, "Unknown error")

    @classmethod
    def invalid_bvid(cls, bvid: Optional[str] = None) -> "APIError":
        msg = f"Invalid BVID format: {bvid}" if bvid else ErrorMessage.INVALID_BVID
        return cls(ErrorCode.INVALID_BVID, msg, 400)

    @classmethod
    def video_not_found(cls, bvid: Optional[str] = None) -> "APIError":
        msg = f"Video not found: {bvid}" if bvid else ErrorMessage.VIDEO_NOT_FOUND
        return cls(ErrorCode.VIDEO_NOT_FOUND, msg, 404)

    @classmethod
    def invalid_input(cls, detail: Optional[str] = None) -> "APIError":
        return cls(ErrorCode.INVALID_INPUT, detail or ErrorMessage.INVALID_INPUT, 400)

    @classmethod
    def validation_error(cls, detail: Optional[str] = None) -> "APIError":
        return cls(ErrorCode.VALIDATION_ERROR, detail or ErrorMessage.VALIDATION_ERROR, 400)

    @classmethod
    def not_found(cls, detail: Optional[str] = None) -> "APIError":
        return cls(ErrorCode.NOT_FOUND, detail or ErrorMessage.NOT_FOUND, 404)

    @classmethod
    def conflict(cls, detail: Optional[str] = None) -> "APIError":
        return cls(ErrorCode.CONFLICT, detail or ErrorMessage.CONFLICT, 409)

    @classmethod
    def auth_required(cls, detail: Optional[str] = None) -> "APIError":
        return cls(ErrorCode.AUTH_REQUIRED, detail or ErrorMessage.AUTH_REQUIRED, 401)

    @classmethod
    def forbidden(cls, detail: Optional[str] = None) -> "APIError":
        return cls(ErrorCode.FORBIDDEN, detail or ErrorMessage.FORBIDDEN, 403)

    @classmethod
    def network_error(cls, detail: Optional[str] = None) -> "APIError":
        msg = f"Network error: {detail}" if detail else ErrorMessage.NETWORK_ERROR
        return cls(ErrorCode.NETWORK_ERROR, msg, 502)

    @classmethod
    def request_timeout(cls, target: Optional[str] = None) -> "APIError":
        msg = f"Request timeout: {target}" if target else ErrorMessage.REQUEST_TIMEOUT
        return cls(ErrorCode.REQUEST_TIMEOUT, msg, 504)

    @classmethod
    def api_error(cls, message: Optional[str] = None) -> "APIError":
        return cls(ErrorCode.API_ERROR, message or ErrorMessage.API_ERROR, 502)

    @classmethod
    def no_dash_stream(cls) -> "APIError":
        return cls(ErrorCode.NO_DASH_STREAM, ErrorMessage.NO_DASH_STREAM, 502)

    @classmethod
    def no_audio_stream(cls) -> "APIError":
        return cls(ErrorCode.NO_AUDIO_STREAM, ErrorMessage.NO_AUDIO_STREAM, 502)

    @classmethod
    def no_audio_loaded(cls) -> "APIError":
        return cls(ErrorCode.NO_AUDIO_LOADED, ErrorMessage.NO_AUDIO_LOADED, 400)
