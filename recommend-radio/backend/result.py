from __future__ import annotations

from typing import Any, Optional

from flask import Response, jsonify

from error_code import ErrorCode


class Result:
    def __init__(
        self,
        success: bool,
        data: Optional[Any] = None,
        error: Optional[str] = None,
        code: Optional[str] = None,
    ):
        self.success = success
        self.data = data
        self.error = error
        self.code = code

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"success": self.success}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = {
                "code": self.code or ErrorCode.UNKNOWN_ERROR.name,
                "message": self.error,
            }
        return result

    def json(self) -> Response:
        return jsonify(self.to_dict())

    def json_with_status(self, status_code: int = 200) -> tuple[Response, int]:
        return jsonify(self.to_dict()), status_code

    @classmethod
    def ok(cls, data: Any = None) -> "Result":
        return cls(success=True, data=data)

    @classmethod
    def fail(
        cls,
        error: str,
        code: str = ErrorCode.UNKNOWN_ERROR.name,
        data: Optional[Any] = None,
    ) -> "Result":
        return cls(success=False, error=error, code=code, data=data)

    @classmethod
    def bad_request(cls, error: str, code: str = ErrorCode.VALIDATION_ERROR.name) -> tuple[Response, int]:
        return cls(success=False, error=error, code=code).json_with_status(400)

    @classmethod
    def not_found(cls, error: str, code: str = ErrorCode.NOT_FOUND.name) -> tuple[Response, int]:
        return cls(success=False, error=error, code=code).json_with_status(404)

    @classmethod
    def server_error(cls, error: str, code: str = ErrorCode.UNKNOWN_ERROR.name) -> tuple[Response, int]:
        return cls(success=False, error=error, code=code).json_with_status(500)
