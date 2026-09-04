from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from bili_client import BiliClient
from constant import HttpHeader, Stream
from error_code import APIError
from models import AudioStreamInfo, normalize_bvid
from monitoring import audio_stream_closed, audio_stream_opened


_EXPIRED_STREAM_STATUSES = {401, 403, 410}
_STATS_FLUSH_BYTES = 256 * 1024
_STATS_FLUSH_SECONDS = 1.0


class _AudioInfoFlight:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Optional[AudioStreamInfo] = None
        self.error: Optional[BaseException] = None


class StreamService:
    def __init__(
        self,
        bili_client: BiliClient,
        cache_ttl_seconds: int = 20 * 60,
        *,
        session: Optional[requests.Session] = None,
        max_cache_entries: int = 512,
    ):
        self.bili_client = bili_client
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_cache_entries = max(1, int(max_cache_entries))
        self.session = session or requests.Session()
        self._owns_session = session is None
        if self._owns_session:
            adapter = HTTPAdapter(
                pool_connections=16,
                pool_maxsize=64,
                max_retries=0,
                pool_block=True,
            )
            self.session.mount('http://', adapter)
            self.session.mount('https://', adapter)

        self._cache: dict[str, tuple[float, AudioStreamInfo]] = {}
        self._aliases: dict[str, str] = {}
        self._inflight: dict[str, _AudioInfoFlight] = {}
        self._lock = threading.Lock()
        self._stats = {
            'total_bytes': 0,
            'start_time': None,
            'current_session_bytes': 0,
        }

    def get_audio_info(
        self,
        bvid: str,
        cid: Optional[int] = None,
        quality: str = 'auto',
        *,
        force_refresh: bool = False,
    ) -> AudioStreamInfo:
        resolved_cid = cid or self.bili_client.get_video_info(bvid).cid
        alias_key = self._alias_key(bvid, resolved_cid, quality)
        if not force_refresh:
            cached = self._get_cached(alias_key)
            if cached:
                return cached

        with self._lock:
            flight = self._inflight.get(alias_key)
            if flight is None:
                flight = _AudioInfoFlight()
                self._inflight[alias_key] = flight
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            if flight.result is None:
                raise APIError.network_error('Audio metadata fetch did not complete')
            return flight.result

        try:
            # Another request may have populated the cache between the initial
            # lookup and this request becoming the single-flight leader.
            if not force_refresh:
                cached = self._get_cached(alias_key)
                if cached:
                    self._finish_flight(alias_key, flight, result=cached)
                    return cached

            audio_info = self.bili_client.get_audio_stream(
                bvid,
                resolved_cid,
                quality=quality,
            )
            full_key = f'{alias_key}:{audio_info.stream_identity}'
            with self._lock:
                previous_key = self._aliases.get(alias_key)
                if previous_key and previous_key != full_key:
                    self._cache.pop(previous_key, None)
                self._cache[full_key] = (time.time(), audio_info)
                self._aliases[alias_key] = full_key
                self._prune_cache_locked()
        except BaseException as exc:
            self._finish_flight(alias_key, flight, error=exc)
            raise

        self._finish_flight(alias_key, flight, result=audio_info)
        return audio_info

    def _finish_flight(
        self,
        alias_key: str,
        flight: _AudioInfoFlight,
        *,
        result: Optional[AudioStreamInfo] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        with self._lock:
            if self._inflight.get(alias_key) is flight:
                self._inflight.pop(alias_key, None)
            flight.result = result
            flight.error = error
            flight.event.set()

    def proxy_stream(
        self,
        bvid: str,
        cid: Optional[int] = None,
        quality: str = 'auto',
    ):
        from flask import Response, current_app, g, request

        request_started_at = getattr(g, 'request_started_at', time.perf_counter())
        request_id = getattr(g, 'request_id', '-')
        logger = current_app.logger

        resolve_started_at = time.perf_counter()
        resolved_cid = cid or self.bili_client.get_video_info(bvid).cid
        audio_info = self.get_audio_info(bvid, resolved_cid, quality)
        resolve_ms = _elapsed_ms(resolve_started_at)

        headers = HttpHeader.stream_headers(bvid)
        range_header = request.headers.get('Range')
        if range_header:
            headers['Range'] = range_header

        upstream_started_at = time.perf_counter()
        upstream, refreshed = self._open_audio_upstream(
            bvid=bvid,
            cid=resolved_cid,
            quality=quality,
            audio_info=audio_info,
            headers=headers,
        )
        upstream_headers_ms = _elapsed_ms(upstream_started_at)
        response_headers = self._proxy_response_headers(upstream)
        response_headers['Server-Timing'] = (
            f'stream_resolve;dur={resolve_ms:.1f}, '
            f'upstream_headers;dur={upstream_headers_ms:.1f}'
        )
        def generate():
            audio_stream_opened()
            pending_stats_bytes = 0
            last_stats_flush = time.monotonic()
            total_bytes = 0
            first_chunk = True
            outcome = 'completed'
            try:
                for chunk in upstream.iter_content(chunk_size=Stream.CHUNK_SIZE):
                    if not chunk:
                        continue
                    chunk_size = len(chunk)
                    total_bytes += chunk_size
                    pending_stats_bytes += chunk_size
                    if first_chunk:
                        first_chunk = False
                        logger.info(
                            'stream_first_byte request_id=%s bvid=%s cid=%s '
                            'duration_ms=%.1f refreshed=%s',
                            request_id,
                            normalize_bvid(bvid),
                            resolved_cid,
                            _elapsed_ms(request_started_at),
                            refreshed,
                        )
                    now = time.monotonic()
                    if (
                        pending_stats_bytes >= _STATS_FLUSH_BYTES
                        or now - last_stats_flush >= _STATS_FLUSH_SECONDS
                    ):
                        self._record_stream_bytes(pending_stats_bytes)
                        pending_stats_bytes = 0
                        last_stats_flush = now
                    yield chunk
            except GeneratorExit:
                outcome = 'client_closed'
                raise
            except BaseException:
                outcome = 'upstream_error'
                raise
            finally:
                if pending_stats_bytes:
                    self._record_stream_bytes(pending_stats_bytes)
                upstream.close()
                audio_stream_closed(total_bytes, outcome)
                logger.info(
                    'stream_closed request_id=%s bvid=%s cid=%s bytes=%s duration_ms=%.1f',
                    request_id,
                    normalize_bvid(bvid),
                    resolved_cid,
                    total_bytes,
                    _elapsed_ms(request_started_at),
                )

        response = Response(
            generate(),
            status=upstream.status_code,
            headers=response_headers,
        )
        response.call_on_close(upstream.close)
        return response

    def download_audio_to_file(
        self,
        bvid: str,
        cid: Optional[int],
        quality: str,
        target_path: Path,
    ) -> dict[str, object]:
        resolved_cid = cid or self.bili_client.get_video_info(bvid).cid
        resolved_quality = quality or 'auto'
        audio_info = self.get_audio_info(bvid, resolved_cid, resolved_quality)
        upstream, refreshed = self._open_audio_upstream(
            bvid=bvid,
            cid=resolved_cid,
            quality=resolved_quality,
            audio_info=audio_info,
            headers=HttpHeader.stream_headers(bvid),
        )

        bytes_written = 0
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target_path.open('wb') as output:
                for chunk in upstream.iter_content(chunk_size=Stream.CHUNK_SIZE):
                    if not chunk:
                        continue
                    output.write(chunk)
                    bytes_written += len(chunk)
        finally:
            upstream.close()

        return {
            'path': str(target_path),
            'bytes': bytes_written,
            'bvid': normalize_bvid(bvid),
            'cid': resolved_cid,
            'quality': resolved_quality,
            'refreshed': refreshed,
        }

    def get_stats(self) -> dict[str, float | int | None]:
        with self._lock:
            stats = self._stats.copy()
        elapsed = time.time() - stats['start_time'] if stats['start_time'] else 0
        speed = stats['current_session_bytes'] / elapsed if elapsed > 0 else 0
        return {
            'total_bytes': stats['total_bytes'],
            'session_bytes': stats['current_session_bytes'],
            'elapsed_seconds': elapsed,
            'bytes_per_second': speed,
            'total_mb': round(stats['total_bytes'] / 1024 / 1024, 2),
            'session_mb': round(stats['current_session_bytes'] / 1024 / 1024, 2),
        }

    def reset_stats(self) -> None:
        with self._lock:
            self._stats['total_bytes'] = 0
            self._stats['current_session_bytes'] = 0
            self._stats['start_time'] = time.time()

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def _open_audio_upstream(
        self,
        *,
        bvid: str,
        cid: int,
        quality: str,
        audio_info: AudioStreamInfo,
        headers: dict[str, str],
    ) -> tuple[requests.Response, bool]:
        upstream, expired, status, error = self._try_stream_urls(audio_info, headers)
        if upstream is not None:
            return upstream, False

        if expired:
            self._invalidate_audio_info(bvid, cid, quality)
            refreshed_info = self.get_audio_info(
                bvid,
                cid,
                quality,
                force_refresh=True,
            )
            upstream, _expired, status, error = self._try_stream_urls(
                refreshed_info,
                headers,
            )
            if upstream is not None:
                return upstream, True

        if isinstance(error, requests.Timeout):
            raise APIError.request_timeout(bvid)
        if error is not None:
            raise APIError.network_error(type(error).__name__)
        if status is not None:
            raise APIError.api_error(f'Audio upstream HTTP {status}')
        raise APIError.network_error('No usable audio upstream URL')

    def _try_stream_urls(
        self,
        audio_info: AudioStreamInfo,
        headers: dict[str, str],
    ) -> tuple[
        Optional[requests.Response],
        bool,
        Optional[int],
        Optional[Exception],
    ]:
        expired = False
        last_status: Optional[int] = None
        last_error: Optional[Exception] = None
        for url in self._candidate_urls(audio_info):
            try:
                response = self.session.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=(5, Stream.TIMEOUT),
                    allow_redirects=True,
                )
            except requests.Timeout as exc:
                last_error = exc
                continue
            except requests.RequestException as exc:
                last_error = exc
                continue

            last_error = None
            if 200 <= response.status_code < 300:
                return response, expired, response.status_code, None

            last_status = response.status_code
            expired = expired or response.status_code in _EXPIRED_STREAM_STATUSES
            response.close()

        return None, expired, last_status, last_error

    @staticmethod
    def _candidate_urls(audio_info: AudioStreamInfo) -> list[str]:
        result: list[str] = []
        for value in [audio_info.url, *audio_info.backup_urls]:
            url = str(value or '').strip()
            if url and url not in result:
                result.append(url)
        return result

    def _get_cached(self, alias_key: str) -> Optional[AudioStreamInfo]:
        with self._lock:
            full_key = self._aliases.get(alias_key)
            if not full_key:
                return None
            created_at, audio_info = self._cache.get(full_key, (0, None))
            if not audio_info:
                self._aliases.pop(alias_key, None)
                return None
            if time.time() - created_at > self.cache_ttl_seconds:
                self._cache.pop(full_key, None)
                self._aliases.pop(alias_key, None)
                return None
            return audio_info

    def _invalidate_audio_info(self, bvid: str, cid: int, quality: str) -> None:
        alias_key = self._alias_key(bvid, cid, quality)
        with self._lock:
            full_key = self._aliases.pop(alias_key, None)
            if full_key:
                self._cache.pop(full_key, None)

    def _prune_cache_locked(self) -> None:
        cutoff = time.time() - self.cache_ttl_seconds
        expired_keys = {
            key for key, (created_at, _value) in self._cache.items() if created_at < cutoff
        }
        for key in expired_keys:
            self._cache.pop(key, None)
        if expired_keys:
            self._aliases = {
                alias: key for alias, key in self._aliases.items() if key not in expired_keys
            }

        while len(self._cache) > self.max_cache_entries:
            oldest_key = min(self._cache, key=lambda key: self._cache[key][0])
            self._cache.pop(oldest_key, None)
            self._aliases = {
                alias: key for alias, key in self._aliases.items() if key != oldest_key
            }

    def _record_stream_bytes(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        with self._lock:
            if self._stats['start_time'] is None:
                self._stats['start_time'] = time.time()
            self._stats['total_bytes'] += byte_count
            self._stats['current_session_bytes'] += byte_count

    def _alias_key(self, bvid: str, cid: int, quality: str) -> str:
        scope_provider = getattr(self.bili_client, 'cache_scope', None)
        scope = scope_provider() if callable(scope_provider) else 'shared'
        return f'{scope}:{normalize_bvid(bvid)}:{int(cid)}:{quality or "auto"}'

    @staticmethod
    def _proxy_response_headers(upstream: requests.Response) -> dict[str, str]:
        headers = {}
        for name in (
            'Content-Type',
            'Content-Length',
            'Content-Range',
            'Accept-Ranges',
            'Content-Encoding',
            'ETag',
            'Last-Modified',
        ):
            if name in upstream.headers:
                headers[name] = upstream.headers[name]
        content_type = headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
        if not content_type or content_type == 'application/octet-stream':
            headers['Content-Type'] = 'audio/mp4'
        headers.setdefault('Accept-Ranges', HttpHeader.ACCEPT_RANGES)
        return headers


def _elapsed_ms(started_at: float) -> float:
    return max(0.0, (time.perf_counter() - started_at) * 1_000)
