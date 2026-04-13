import type { JobPayload } from './types'

export const apiKeyStorageKey = 'miscoshorts.apiKey'
export const jobIdStorageKey = 'miscoshorts.activeJobId'

// Connection state management
export type ConnectionState = 'connected' | 'reconnecting' | 'disconnected'

export function loadSavedJobId(): string | null {
  try {
    return window.localStorage.getItem(jobIdStorageKey) ?? null
  } catch {
    return null
  }
}

export function savePendingJobId(jobId: string | null) {
  try {
    if (jobId) {
      window.localStorage.setItem(jobIdStorageKey, jobId)
    } else {
      window.localStorage.removeItem(jobIdStorageKey)
    }
  } catch {
    // Ignore storage failures
  }
}

export function loadSavedApiKey() {
  try {
    return window.localStorage.getItem(apiKeyStorageKey) ?? ''
  } catch {
    return ''
  }
}

/**
 * Safe JSON response parser with better error messages.
 */
export async function readJsonResponse<T>(response: Response): Promise<T> {
  const raw = await response.text()

  try {
    return JSON.parse(raw) as T
  } catch {
    const snippet = raw.trim().slice(0, 180)
    if (snippet.startsWith('<!doctype html') || snippet.startsWith('<html')) {
      throw new Error('The local app returned HTML instead of API JSON. Restart the app from the launcher and refresh the page.')
    }
    throw new Error(snippet || `The local app returned an unreadable response (${response.status}).`)
  }
}

/**
 * Safe fetch wrapper with timeout and better error handling.
 */
export async function safeFetch(
  url: string,
  options: RequestInit & { timeoutMs?: number } = {}
): Promise<Response> {
  const { timeoutMs = 10000, signal: externalSignal, ...fetchOptions } = options
  
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  
  // Combine external signal with timeout signal
  const combinedSignal = externalSignal 
    ? anySignal([externalSignal, controller.signal])
    : controller.signal
  
  try {
    const response = await fetch(url, { ...fetchOptions, signal: combinedSignal })
    clearTimeout(timeoutId)
    return response
  } catch (error) {
    clearTimeout(timeoutId)
    if (error instanceof DOMException && error.name === 'AbortError') {
      // Check if it was our timeout or external abort
      if (controller.signal.aborted && !externalSignal?.aborted) {
        throw new Error(`Request timed out after ${timeoutMs}ms`)
      }
    }
    throw error
  }
}

/**
 * Combine multiple AbortSignals into one.
 */
function anySignal(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController()
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason)
      return controller.signal
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  return controller.signal
}

/**
 * Calculate exponential backoff delay with jitter.
 */
export function getBackoffDelay(attempt: number, baseMs = 1000, maxMs = 30000): number {
  const exponential = Math.min(maxMs, baseMs * Math.pow(2, attempt))
  const jitter = Math.random() * 0.3 * exponential // 30% jitter
  return Math.round(exponential + jitter)
}

/**
 * Deep comparison for job payloads to detect meaningful changes.
 */
export function jobPayloadChanged(prev: JobPayload | null, next: JobPayload): boolean {
  if (!prev) return true
  
  // Check critical fields that should trigger updates
  if (prev.status !== next.status) return true
  if (prev.overallProgress !== next.overallProgress) return true
  if (prev.stageProgress !== next.stageProgress) return true
  if (prev.etaSeconds !== next.etaSeconds) return true
  if (prev.message !== next.message) return true
  if (prev.error !== next.error) return true
  if ((prev.logs?.length ?? 0) !== (next.logs?.length ?? 0)) return true
  if (!prev.result && next.result) return true
  if (prev.queuePosition !== next.queuePosition) return true
  if (prev.queueState !== next.queueState) return true
  
  return false
}

export function formatLogTime(timestamp: number) {
  return new Intl.DateTimeFormat('sv-SE', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(timestamp * 1000))
}

export function formatEta(seconds: number) {
  if (seconds <= 45) {
    return 'under 1 min'
  }

  const minutes = Math.ceil(seconds / 60)
  if (minutes < 60) {
    return `${minutes} min`
  }

  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`
}

export function formatBytes(numBytes: number) {
  if (numBytes >= 1024 * 1024 * 1024) {
    return `${(numBytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
  }
  if (numBytes >= 1024 * 1024) {
    return `${Math.round(numBytes / (1024 * 1024))} MB`
  }
  if (numBytes >= 1024) {
    return `${Math.round(numBytes / 1024)} KB`
  }
  return `${numBytes} B`
}

export function getEtaWindow(job: JobPayload, selectedClipCount: number, nowMs: number) {
  const effectiveClipCount = job.result?.clipCount ?? job.clipCount ?? selectedClipCount
  const stageStartedAt = (job.updatedAt ?? job.createdAt ?? nowMs / 1000) * 1000
  const elapsedSeconds = Math.max(0, Math.round((nowMs - stageStartedAt) / 1000))

  switch (job.status) {
    case 'queued':
      return [Math.max(0, 20 - elapsedSeconds), Math.max(0, 60 - elapsedSeconds)]
    case 'validating':
      return [Math.max(0, 5 - elapsedSeconds), Math.max(0, 20 - elapsedSeconds)]
    case 'downloading':
      return [Math.max(0, 40 - elapsedSeconds), Math.max(0, 150 - elapsedSeconds)]
    case 'transcribing':
      return [Math.max(0, 150 - elapsedSeconds), Math.max(0, 540 - elapsedSeconds)]
    case 'analyzing':
      return [Math.max(0, 20 - elapsedSeconds), Math.max(0, 120 - elapsedSeconds)]
    case 'rendering':
      return [Math.max(0, 90 * effectiveClipCount - elapsedSeconds), Math.max(0, 240 * effectiveClipCount - elapsedSeconds)]
    default:
      return null
  }
}

/**
 * Estimate total job time before starting (in minutes).
 * Returns [minMinutes, maxMinutes] based on clip count.
 */
export function estimateTotalJobTime(clipCount: number): [number, number] {
  // Typical phases:
  // - Download: 30-120s depending on video length
  // - Transcribe: 120-480s (whisper is slow)
  // - Analyze: 15-60s (Gemini)
  // - Render: 60-180s per clip
  const downloadRange = [30, 120]
  const transcribeRange = [120, 480]
  const analyzeRange = [15, 60]
  const renderPerClip = [60, 180]
  
  const minSeconds = downloadRange[0] + transcribeRange[0] + analyzeRange[0] + (renderPerClip[0] * clipCount)
  const maxSeconds = downloadRange[1] + transcribeRange[1] + analyzeRange[1] + (renderPerClip[1] * clipCount)
  
  return [Math.ceil(minSeconds / 60), Math.ceil(maxSeconds / 60)]
}

/**
 * Shallow compare two objects to detect meaningful changes.
 * Returns true if objects are effectively equal.
 */
export function shallowEqual<T extends Record<string, unknown>>(a: T | null, b: T | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  const keysA = Object.keys(a)
  const keysB = Object.keys(b)
  if (keysA.length !== keysB.length) return false
  for (const key of keysA) {
    if (a[key] !== b[key]) return false
  }
  return true
}
