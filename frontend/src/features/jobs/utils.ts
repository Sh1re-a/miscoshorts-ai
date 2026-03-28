import type { JobPayload } from './types'

export const apiKeyStorageKey = 'miscoshorts.apiKey'

export function loadSavedApiKey() {
  try {
    return window.localStorage.getItem(apiKeyStorageKey) ?? ''
  } catch {
    return ''
  }
}

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
