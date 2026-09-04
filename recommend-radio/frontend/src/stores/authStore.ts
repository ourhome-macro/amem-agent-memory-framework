import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  createBiliLoginQr,
  fetchAppSession,
  fetchAuthStatus,
  logoutAppSession,
  logoutBili,
  pollBiliLoginQr,
  redirectToOidcLogin,
  setApiCsrfToken,
} from '@/api/client'
import type {
  AppSession,
  AuthQrCode,
  AuthQrStatus,
  AuthStatus,
  BiliUserProfile,
} from '@/types'

const defaultAppSession: AppSession = {
  authenticated: false,
  user: null,
  csrfToken: null,
  oidcEnabled: true,
  biliConnected: false,
}

const defaultBiliStatus: AuthStatus = {
  qrLoginEnabled: true,
  isLoggedIn: false,
  user: null,
  cookieUpdatedAt: null,
}

export const useAuthStore = defineStore('auth', () => {
  const appSession = ref<AppSession>(defaultAppSession)
  const biliStatus = ref<AuthStatus>(defaultBiliStatus)
  const qrCode = ref<AuthQrCode | null>(null)
  const qrStatus = ref<AuthQrStatus | null>(null)
  const isSessionChecking = ref(false)
  const isBiliChecking = ref(false)
  const isQrLoading = ref(false)
  const sessionError = ref<string | null>(null)
  const biliError = ref<string | null>(null)
  const hasSessionLoaded = ref(false)
  const hasBiliLoaded = ref(false)

  let sessionPromise: Promise<AppSession> | null = null
  let biliStatusPromise: Promise<AuthStatus> | null = null

  const appAuthenticated = computed(() => appSession.value.authenticated)
  const appUser = computed(() => appSession.value.user)
  const isAdmin = computed(() => appSession.value.user?.role === 'admin')
  const biliConnected = computed(
    () => biliStatus.value.isLoggedIn || appSession.value.biliConnected
  )
  const biliUser = computed<BiliUserProfile | null>(() => biliStatus.value.user)

  async function initializeSession(refresh = false): Promise<AppSession> {
    if (hasSessionLoaded.value && !refresh) return appSession.value
    if (sessionPromise) return sessionPromise

    isSessionChecking.value = true
    sessionError.value = null
    sessionPromise = fetchAppSession()
      .then((data) => {
        appSession.value = data
        hasSessionLoaded.value = true
        setApiCsrfToken(data.csrfToken)
        return data
      })
      .catch((error) => {
        appSession.value = defaultAppSession
        hasSessionLoaded.value = false
        setApiCsrfToken(null)
        sessionError.value = error instanceof Error ? error.message : '应用登录状态检查失败'
        throw error
      })
      .finally(() => {
        isSessionChecking.value = false
        sessionPromise = null
      })
    return sessionPromise
  }

  async function initializeBili(refresh = false): Promise<AuthStatus> {
    if (hasBiliLoaded.value && !refresh) return biliStatus.value
    if (biliStatusPromise) return biliStatusPromise

    isBiliChecking.value = true
    biliError.value = null
    biliStatusPromise = fetchAuthStatus(refresh)
      .then((data) => {
        biliStatus.value = data
        appSession.value = { ...appSession.value, biliConnected: data.isLoggedIn }
        hasBiliLoaded.value = true
        return data
      })
      .catch((error) => {
        biliStatus.value = defaultBiliStatus
        hasBiliLoaded.value = true
        biliError.value = error instanceof Error ? error.message : 'B 站登录状态检查失败'
        return biliStatus.value
      })
      .finally(() => {
        isBiliChecking.value = false
        biliStatusPromise = null
      })
    return biliStatusPromise
  }

  async function startQrLogin(): Promise<AuthQrCode> {
    isQrLoading.value = true
    biliError.value = null
    qrStatus.value = null
    try {
      qrCode.value = await createBiliLoginQr()
      return qrCode.value
    } catch (error) {
      biliError.value = error instanceof Error ? error.message : '二维码生成失败'
      throw error
    } finally {
      isQrLoading.value = false
    }
  }

  async function pollQrLogin(): Promise<AuthQrStatus | null> {
    if (!qrCode.value) return null
    try {
      const nextStatus = await pollBiliLoginQr(qrCode.value.qrcodeKey)
      qrStatus.value = nextStatus
      if (nextStatus.status === 'confirmed') {
        biliStatus.value = {
          qrLoginEnabled: true,
          isLoggedIn: true,
          user: nextStatus.user,
          cookieUpdatedAt: new Date().toISOString(),
        }
        appSession.value = { ...appSession.value, biliConnected: true }
        hasBiliLoaded.value = true
      }
      return nextStatus
    } catch (error) {
      biliError.value = error instanceof Error ? error.message : '扫码状态检查失败'
      return null
    }
  }

  async function disconnectBili(): Promise<void> {
    await logoutBili()
    biliStatus.value = defaultBiliStatus
    appSession.value = { ...appSession.value, biliConnected: false }
    qrCode.value = null
    qrStatus.value = null
    hasBiliLoaded.value = true
  }

  async function logoutApp(): Promise<void> {
    await logoutAppSession()
    appSession.value = defaultAppSession
    biliStatus.value = defaultBiliStatus
    hasSessionLoaded.value = true
    hasBiliLoaded.value = false
    setApiCsrfToken(null)
  }

  function loginWithOidc(next?: string): void {
    redirectToOidcLogin(next)
  }

  return {
    appSession,
    biliStatus,
    qrCode,
    qrStatus,
    isSessionChecking,
    isBiliChecking,
    isQrLoading,
    sessionError,
    biliError,
    hasSessionLoaded,
    hasBiliLoaded,
    appAuthenticated,
    appUser,
    isAdmin,
    biliConnected,
    biliUser,
    initializeSession,
    initializeBili,
    startQrLogin,
    pollQrLogin,
    disconnectBili,
    logoutApp,
    loginWithOidc,
  }
})
