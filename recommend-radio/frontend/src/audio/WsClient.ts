import { io, Socket } from 'socket.io-client'
import { getApiBaseUrl } from '@/api/client'
import type {
  VideoInfo,
  AudioDataPacket,
  AudioStreamInfo,
  DownloadProgress,
  PlaybackProgress
} from '@/types'

type VideoInfoCallback = (info: VideoInfo) => void
type AudioDataCallback = (data: AudioDataPacket) => void
type AudioStreamCallback = (data: AudioStreamInfo) => void
type DownloadProgressCallback = (progress: DownloadProgress) => void
type PlaybackProgressCallback = (progress: PlaybackProgress) => void
type StatusCallback = (message: string) => void
type ErrorCallback = (message: string) => void
type StateCallback = (state: string) => void

class WebSocketClient {
  private socket: Socket | null = null
  private connected = false

  private onVideoInfo: VideoInfoCallback | null = null
  private onAudioData: AudioDataCallback | null = null
  private onAudioStream: AudioStreamCallback | null = null
  private onDownloadProgress: DownloadProgressCallback | null = null
  private onPlaybackProgress: PlaybackProgressCallback | null = null
  private onStatus: StatusCallback | null = null
  private onError: ErrorCallback | null = null
  private onProducerState: StateCallback | null = null
  private onConsumerState: StateCallback | null = null

  connect(url: string = import.meta.env.VITE_SOCKET_URL ?? defaultSocketUrl()): Promise<boolean> {
    return new Promise((resolve) => {
      this.socket = io(url, {
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionAttempts: 5,
        reconnectionDelay: 1000,
      })

      this.socket.on('connect', () => {
        this.connected = true
        console.log('WebSocket connected')
        resolve(true)
      })

      this.socket.on('disconnect', () => {
        this.connected = false
        console.log('WebSocket disconnected')
      })

      this.socket.on('connect_error', (error) => {
        console.error('WebSocket connection error:', error)
        resolve(false)
      })

      this.socket.on('connected', (data) => {
        console.log('Server confirmed connection:', data.message)
      })

      this.socket.on('video_info', (data: VideoInfo) => {
        if (this.onVideoInfo) {
          this.onVideoInfo(data)
        }
      })

      this.socket.on('audio_stream', (data: AudioStreamInfo) => {
        if (this.onAudioStream) {
          this.onAudioStream(data)
        }
      })

      this.socket.on('audio_data', (data: AudioDataPacket) => {
        if (this.onAudioData) {
          this.onAudioData(data)
        }
      })

      this.socket.on('download_progress', (data: DownloadProgress) => {
        if (this.onDownloadProgress) {
          this.onDownloadProgress(data)
        }
      })

      this.socket.on('playback_progress', (data: PlaybackProgress) => {
        if (this.onPlaybackProgress) {
          this.onPlaybackProgress(data)
        }
      })

      this.socket.on('status', (data: { message: string }) => {
        if (this.onStatus) {
          this.onStatus(data.message)
        }
      })

      this.socket.on('error', (data: { message: string }) => {
        if (this.onError) {
          this.onError(data.message)
        }
      })

      this.socket.on('producer_state', (data: { state: string }) => {
        if (this.onProducerState) {
          this.onProducerState(data.state)
        }
      })

      this.socket.on('consumer_state', (data: { state: string }) => {
        if (this.onConsumerState) {
          this.onConsumerState(data.state)
        }
      })
    })
  }

  disconnect() {
    if (this.socket) {
      this.socket.disconnect()
      this.socket = null
      this.connected = false
    }
  }

  isConnected(): boolean {
    return this.connected
  }

  setCallbacks(options: {
    onVideoInfo?: VideoInfoCallback
    onAudioData?: AudioDataCallback
    onAudioStream?: AudioStreamCallback
    onDownloadProgress?: DownloadProgressCallback
    onPlaybackProgress?: PlaybackProgressCallback
    onStatus?: StatusCallback
    onError?: ErrorCallback
    onProducerState?: StateCallback
    onConsumerState?: StateCallback
  }) {
    this.onVideoInfo = options.onVideoInfo || null
    this.onAudioData = options.onAudioData || null
    this.onAudioStream = options.onAudioStream || null
    this.onDownloadProgress = options.onDownloadProgress || null
    this.onPlaybackProgress = options.onPlaybackProgress || null
    this.onStatus = options.onStatus || null
    this.onError = options.onError || null
    this.onProducerState = options.onProducerState || null
    this.onConsumerState = options.onConsumerState || null
  }

  playVideo(input: string) {
    console.log('[WsClient] playVideo called, connected:', this.connected, 'input:', input)
    if (this.socket && this.connected) {
      console.log('[WsClient] emitting play_video event')
      this.socket.emit('play_video', { input })
    } else {
      console.error('[WsClient] Cannot play video: socket=', !!this.socket, 'connected=', this.connected)
    }
  }

  pause() {
    if (this.socket && this.connected) {
      this.socket.emit('pause')
    }
  }

  resume() {
    if (this.socket && this.connected) {
      this.socket.emit('resume')
    }
  }

  stop() {
    if (this.socket && this.connected) {
      this.socket.emit('stop')
    }
  }

  seek(timeSeconds: number) {
    if (this.socket && this.connected) {
      this.socket.emit('seek', { time: timeSeconds })
    }
  }

  getStatus() {
    if (this.socket && this.connected) {
      this.socket.emit('get_status')
    }
  }
}

function defaultSocketUrl(): string {
  return getApiBaseUrl() || window.location.origin
}

export const wsClient = new WebSocketClient()
