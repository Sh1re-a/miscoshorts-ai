import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, Clock, Download, HardDrive, List, LoaderCircle, Play, PlaySquare, RefreshCw, RotateCcw, StopCircle, ThumbsUp, ThumbsDown, BarChart3, Trash2 } from 'lucide-react'

import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Label } from './components/ui/label'
import { Progress } from './components/ui/progress'
import { feedbackTags, progressByStatus, stageDescriptions, statusTitles } from './features/jobs/config'
import type { AnalyticsInsights, BootstrapPayload, ClipFeedback, DoctorReport, JobPayload, JobStatus, ProcessErrorPayload, PruneResult, RuntimePayload, StorageReport } from './features/jobs/types'
import { apiKeyStorageKey, estimateTotalJobTime, formatBytes, formatEta, formatLogTime, getBackoffDelay, getEtaWindow, jobPayloadChanged, loadSavedApiKey, loadSavedJobId, readJsonResponse, safeFetch, savePendingJobId } from './features/jobs/utils'

const fallbackRenderProfile = 'studio'
const fallbackRenderProfiles = {
  fast: 'Fast Draft 1080x1920 MP4',
  balanced: 'Balanced 1080x1920 MP4',
  studio: 'Studio HQ 1080x1920 MP4',
}
const lockedSubtitleStyle = { fontPreset: 'soft', colorPreset: 'editorial' } as const

function App() {
  const [videoUrl, setVideoUrl] = useState('')
  const [apiKey, setApiKey] = useState(loadSavedApiKey)
  const [hasConfiguredApiKey, setHasConfiguredApiKey] = useState(false)
  const [renderProfiles, setRenderProfiles] = useState<Record<string, string>>(fallbackRenderProfiles)
  const [selectedRenderProfile, setSelectedRenderProfile] = useState(fallbackRenderProfile)
  const [speakerMode, setSpeakerMode] = useState('auto')
  const [outputFilename, setOutputFilename] = useState('short_con_subs.mp4')
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobPayload>({ status: 'idle' })
  const [requestError, setRequestError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [apiKeyNotice, setApiKeyNotice] = useState(apiKey ? 'Saved locally in this browser.' : 'Not saved yet.')
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [clipFeedback, setClipFeedback] = useState<Record<number, ClipFeedback>>({})
  const [analyticsData, setAnalyticsData] = useState<AnalyticsInsights | null>(null)
  const [analyticsError, setAnalyticsError] = useState(false)
  const [showAnalytics, setShowAnalytics] = useState(false)
  const [selectedClipCount, setSelectedClipCount] = useState(3)
  const [cleanupConfirm, setCleanupConfirm] = useState<null | 'source' | 'job'>(null)
  const [sourceMediaDeleted, setSourceMediaDeleted] = useState(false)
  const [cleanupActionError, setCleanupActionError] = useState<string | null>(null)
  const [previewClipIndex, setPreviewClipIndex] = useState<number | null>(null)
  const [storageReport, setStorageReport] = useState<StorageReport | null>(null)
  const [storageLoading, setStorageLoading] = useState(false)
  const [showStorage, setShowStorage] = useState(false)
  const [pruneWorking, setPruneWorking] = useState(false)
  const [lastPruneResult, setLastPruneResult] = useState<string | null>(null)
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null)
  const [runtimeState, setRuntimeState] = useState<RuntimePayload | null>(null)
  const [knownRuntimeSessionId, setKnownRuntimeSessionId] = useState<string | null>(null)
  const [isReconnecting, setIsReconnecting] = useState(false)
  const [isCancelling, setIsCancelling] = useState(false)
  const pollErrorCountRef = useRef(0)
  const runtimeErrorCountRef = useRef(0)
  const jobIdRef = useRef<string | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
  const runtimeAbortRef = useRef<AbortController | null>(null)
  const prevRuntimeRef = useRef<RuntimePayload | null>(null)
  const prevJobRef = useRef<JobPayload | null>(null)
  const pollIntervalRef = useRef<number | null>(null)
  const runtimeIntervalRef = useRef<number | null>(null)

  // Keep ref in sync so async callbacks can read the latest value
  useEffect(() => { jobIdRef.current = jobId }, [jobId])

  // Persist jobId to localStorage so it survives page reload
  useEffect(() => {
    savePendingJobId(jobId)
  }, [jobId])

  // On startup, restore jobId from localStorage if present
  useEffect(() => {
    const savedJobId = loadSavedJobId()
    if (savedJobId && !jobId) {
      setJobId(savedJobId)
      setJob({ status: 'queued', message: 'Reconnecting to job...' })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false

    async function loadBootstrap() {
      try {
        const response = await fetch('/api/bootstrap')
        if (!response.ok) {
          return
        }

        const payload = await readJsonResponse<BootstrapPayload>(response)
        if (!cancelled) {
          setHasConfiguredApiKey(payload.hasConfiguredApiKey)
          setRenderProfiles(payload.renderProfiles ?? fallbackRenderProfiles)
          setSelectedRenderProfile(payload.defaultRenderProfile || fallbackRenderProfile)
          setSpeakerMode(payload.speakerDiarizationMode ?? 'auto')
          setKnownRuntimeSessionId(payload.runtimeSessionId)
        }
      } catch {
        // Keep the app usable even if the bootstrap request fails.
      }
    }

    async function loadDoctor() {
      try {
        const response = await fetch('/api/doctor')
        if (!response.ok) {
          return
        }
        const payload = await readJsonResponse<DoctorReport>(response)
        if (!cancelled) {
          setDoctorReport(payload)
        }
      } catch {
        // Keep the app usable even if diagnostics fail.
      }
    }

    void loadBootstrap()
    void loadDoctor()

    return () => {
      cancelled = true
    }
  }, [])

  const loadRuntimeSnapshot = useEffectEvent(async () => {
    // Cancel any in-flight request
    runtimeAbortRef.current?.abort()
    const controller = new AbortController()
    runtimeAbortRef.current = controller
    
    try {
      const response = await safeFetch('/api/runtime', { 
        signal: controller.signal,
        timeoutMs: 8000 
      })
      
      if (!response.ok) {
        throw new Error(`Runtime API returned ${response.status}`)
      }

      const payload = await readJsonResponse<RuntimePayload>(response)
      
      // Reset error count on success
      runtimeErrorCountRef.current = 0
      
      // Only update state if meaningful data changed to reduce flickering
      const prev = prevRuntimeRef.current
      const queueChanged = !prev || 
        prev.queue.activeCount !== payload.queue.activeCount ||
        prev.queue.queuedCount !== payload.queue.queuedCount ||
        prev.consistency.status !== payload.consistency.status
      
      const jobsChanged = !prev ||
        (prev.recentJobs?.length ?? 0) !== (payload.recentJobs?.length ?? 0) ||
        prev.recentJobs?.some((j, i) => j.status !== payload.recentJobs?.[i]?.status)
      
      if (queueChanged || jobsChanged || prev?.runtimeSessionId !== payload.runtimeSessionId) {
        prevRuntimeRef.current = payload
        setRuntimeState(payload)
        setKnownRuntimeSessionId(payload.runtimeSessionId)
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      
      runtimeErrorCountRef.current += 1
      // Only log after multiple failures to avoid console spam
      if (runtimeErrorCountRef.current === 3) {
        console.warn('Runtime polling experiencing issues:', error)
      }
    }
  })

  useEffect(() => {
    void loadRuntimeSnapshot()

    // Use a stable interval with longer delay to reduce server load
    runtimeIntervalRef.current = window.setInterval(() => {
      void loadRuntimeSnapshot()
    }, 2500) // Slightly longer interval for stability

    return () => {
      if (runtimeIntervalRef.current) {
        window.clearInterval(runtimeIntervalRef.current)
        runtimeIntervalRef.current = null
      }
      runtimeAbortRef.current?.abort()
      runtimeAbortRef.current = null
    }
  }, [loadRuntimeSnapshot])

  const pollJob = useEffectEvent(async () => {
    if (!jobId) {
      return
    }

    // Abort any previous in-flight poll before starting a new one
    pollAbortRef.current?.abort()
    const controller = new AbortController()
    pollAbortRef.current = controller

    try {
      const response = await safeFetch(`/api/jobs/${jobId}`, { 
        signal: controller.signal,
        timeoutMs: 10000
      })
      
      // Guard: if jobId was cleared while the fetch was in-flight, discard the response
      if (jobIdRef.current !== jobId) return

      // Check 404 BEFORE parsing JSON — the body may not be valid JSON
      if (response.status === 404) {
        const payload = await readJsonResponse<JobPayload>(response).catch(() => ({} as JobPayload))
        const latestRuntimeSessionId = runtimeState?.runtimeSessionId ?? knownRuntimeSessionId
        const runtimeChanged = Boolean(job.runtimeSessionId && latestRuntimeSessionId && job.runtimeSessionId !== latestRuntimeSessionId)
        setJob({
          status: 'failed',
          error: runtimeChanged
            ? 'The local backend restarted and cleared the old live queue state.'
            : payload.error ?? 'This job is no longer present in the local backend.',
          errorHelp: 'Start the render again from this page. The backend queue state is now the source of truth.',
          runtimeSessionId: latestRuntimeSessionId ?? job.runtimeSessionId,
        })
        setJobId(null)
        return
      }

      const payload = await readJsonResponse<JobPayload>(response)

      if (!response.ok) {
        throw new Error(payload.error ?? 'Could not load job status.')
      }

      // Success - reset error state
      pollErrorCountRef.current = 0
      setIsReconnecting(false)
      
      // Only update job state if meaningful fields changed to reduce flickering
      if (jobPayloadChanged(prevJobRef.current, payload)) {
        prevJobRef.current = payload
        setJob(payload)
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      
      pollErrorCountRef.current += 1
      
      if (pollErrorCountRef.current >= 2 && pollErrorCountRef.current < 15) {
        setIsReconnecting(true)
      }
      
      if (pollErrorCountRef.current >= 15) {
        setIsReconnecting(false)
        const message = error instanceof Error ? error.message : 'Could not refresh the live job status.'
        setJob((previous) =>
          previous.status === 'completed'
            ? previous
            : {
                ...previous,
                status: 'failed',
                error: message,
                errorHelp: 'The app lost contact with the backend. Check that the server is running, then start a new render.',
              },
        )
      }
    }
  })

  useEffect(() => {
    if (!jobId) {
      // Abort any in-flight poll when jobId becomes null (e.g. resetFlow)
      pollAbortRef.current?.abort()
      pollAbortRef.current = null
      if (pollIntervalRef.current) {
        window.clearTimeout(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
      return
    }

    // Initial poll
    void pollJob()

    // Use adaptive polling: faster during active work, slower otherwise
    const getInterval = () => {
      const isActive = !['idle', 'completed', 'failed'].includes(job.status)
      const hasErrors = pollErrorCountRef.current > 0
      
      if (hasErrors) {
        // Backoff on errors
        return getBackoffDelay(pollErrorCountRef.current, 1500, 10000)
      }
      return isActive ? 1200 : 3000 // Faster when active, slower when idle
    }

    // Use setTimeout recursion so the delay is re‑evaluated each cycle
    const scheduleNext = () => {
      pollIntervalRef.current = window.setTimeout(() => {
        void Promise.resolve(pollJob()).finally(() => {
          // Only schedule the next tick if we haven't been cleaned up
          if (pollIntervalRef.current !== null) {
            scheduleNext()
          }
        })
      }, getInterval())
    }
    scheduleNext()

    return () => {
      if (pollIntervalRef.current) {
        window.clearTimeout(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
      pollAbortRef.current?.abort()
      pollAbortRef.current = null
    }
  }, [jobId, pollJob, job.status])

  useEffect(() => {
    if (['idle', 'completed', 'failed'].includes(job.status)) {
      return
    }

    const intervalId = window.setInterval(() => {
      setNowMs(Date.now())
    }, 1000)

    return () => window.clearInterval(intervalId)
  }, [job.status])

  useEffect(() => {
    try {
      const trimmedKey = apiKey.trim()
      if (!trimmedKey) {
        window.localStorage.removeItem(apiKeyStorageKey)
        setApiKeyNotice('Not saved yet.')
        return
      }

      window.localStorage.setItem(apiKeyStorageKey, trimmedKey)
      setApiKeyNotice('Saved locally in this browser.')
    } catch {
      setApiKeyNotice('Could not save locally in this browser.')
    }
  }, [apiKey])

  const isWorking = !['idle', 'completed', 'failed'].includes(job.status)
  const recentLogs = useMemo(() => (job.logs ?? []).slice(-8).reverse(), [job.logs])
  const hasAvailableApiKey = Boolean(apiKey.trim()) || hasConfiguredApiKey
  const hasBlockingDoctorFailure = doctorReport?.status === 'FAIL'
  const canSubmit = Boolean(videoUrl.trim()) && hasAvailableApiKey && !hasBlockingDoctorFailure && !isSubmitting && !isWorking
  const etaWindow = useMemo(() => getEtaWindow(job, selectedClipCount, nowMs), [job, selectedClipCount, nowMs])
  const etaLabel = job.etaSeconds != null
    ? formatEta(job.etaSeconds)
    : etaWindow
      ? `${formatEta(etaWindow[0])} to ${formatEta(etaWindow[1])}`
      : null
  // Pre-start time estimate based on clip count
  const preStartEstimate = useMemo(() => estimateTotalJobTime(selectedClipCount), [selectedClipCount])
  const hasStarted = job.status !== 'idle' || isSubmitting || jobId !== null
  const effectiveClipCount = job.result?.clipCount ?? job.clipCount ?? selectedClipCount
  const currentRenderProfileLabel = renderProfiles[selectedRenderProfile] ?? renderProfiles[fallbackRenderProfile] ?? 'Studio HQ 1080x1920 MP4'
  const progressValue = job.overallProgress ?? progressByStatus[job.status]
  const highlightedDoctorChecks = useMemo(
    () => {
      const hasBrowserKey = Boolean(apiKey.trim())
      return (doctorReport?.checks ?? [])
        .filter((check) => check.status !== 'PASS')
        .map((check) => {
          // Gemini key: suppress the warning when user has entered a key in the browser
          if (check.name === 'Gemini API key' && check.status === 'WARN' && hasBrowserKey) {
            return { ...check, status: 'PASS' as const, message: 'Gemini key provided in this browser session.' }
          }
          // Pyannote: rewrite to human-readable language
          if (check.name === 'Pyannote diarization') {
            const friendlyName = 'Speaker separation (advanced)'
            if (check.status === 'WARN') {
              return { ...check, name: friendlyName, message: 'Advanced multi-speaker detection is off — not needed for most videos.' }
            }
            if (check.status === 'FAIL') {
              return { ...check, name: friendlyName, message: 'Advanced speaker splitting is enabled but not fully set up.' }
            }
          }
          return check
        })
        .filter((check) => check.status !== 'PASS')
        .slice(0, 4)
    },
    [doctorReport, apiKey],
  )
  const highlightedStorage = useMemo(
    () => doctorReport?.storage ? Object.entries(doctorReport.storage).slice(0, 4) : [],
    [doctorReport],
  )
  const runtimeIssues = runtimeState?.consistency.issues ?? []
  const runtimeRecoverySummary = useMemo(() => {
    if (!runtimeState?.recovery) return null
    const recoveredJobs = runtimeState.recovery.recoveredJobIds?.length ?? 0
    const clearedLocks = runtimeState.recovery.clearedLocks?.length ?? 0
    const clearedTempWorkspaces = runtimeState.recovery.clearedTempWorkspacePaths?.length ?? 0
    if (!recoveredJobs && !clearedLocks && !clearedTempWorkspaces) return null
    return { recoveredJobs, clearedLocks, clearedTempWorkspaces }
  }, [runtimeState])

  // Combine active and queued jobs, with active ones first
  const visibleJobs = useMemo(() => {
    if (!runtimeState) return []
    const active = runtimeState.queue.activeJobs ?? []
    const queued = runtimeState.queue.queuedJobs ?? []
    const recent = runtimeState.recentJobs ?? []
    // Deduplicate: activeJobs + queuedJobs, then completed from recentJobs
    const seenIds = new Set<string>()
    const result: typeof recent = []
    for (const job of [...active, ...queued]) {
      if (!seenIds.has(job.jobId)) {
        seenIds.add(job.jobId)
        result.push(job)
      }
    }
    // Add recent completed/failed jobs (up to 5 total)
    for (const job of recent) {
      if (result.length >= 5) break
      if (!seenIds.has(job.jobId) && (job.status === 'completed' || job.status === 'failed')) {
        seenIds.add(job.jobId)
        result.push(job)
      }
    }
    return result
  }, [runtimeState])

  function resetFlow() {
    // Clean up job polling interval
    if (pollIntervalRef.current) {
      clearTimeout(pollIntervalRef.current)
      pollIntervalRef.current = null
    }
    // Abort any in-flight job poll
    pollAbortRef.current?.abort()
    pollAbortRef.current = null
    // Reset error counters
    pollErrorCountRef.current = 0
    prevJobRef.current = null
    setIsReconnecting(false)
    setIsCancelling(false)

    setJobId(null)
    setJob({ status: 'idle' })
    setRequestError(null)
    setIsSubmitting(false)
    setOutputFilename('short_con_subs.mp4')

    // Clear per-job UI state
    setClipFeedback({})
    setCleanupConfirm(null)
    setSourceMediaDeleted(false)
    setCleanupActionError(null)
    setPreviewClipIndex(null)
    setAnalyticsData(null)
    setShowAnalytics(false)
  }

  function switchToJob(targetJobId: string, targetJob?: { status?: string; message?: string; runtimeSessionId?: string }) {
    // Clean up job polling interval (will be restarted by useEffect for new job)
    if (pollIntervalRef.current) {
      clearTimeout(pollIntervalRef.current)
      pollIntervalRef.current = null
    }
    // Abort any in-flight job poll
    pollAbortRef.current?.abort()
    pollAbortRef.current = null
    // Reset error counters
    pollErrorCountRef.current = 0
    prevJobRef.current = null
    setIsReconnecting(false)
    setIsCancelling(false)

    // Clear per-job UI state
    setClipFeedback({})
    setCleanupConfirm(null)
    setSourceMediaDeleted(false)
    setCleanupActionError(null)
    setPreviewClipIndex(null)
    setAnalyticsData(null)
    setShowAnalytics(false)
    setRequestError(null)
    setIsSubmitting(false)

    // Switch to new job
    setJobId(targetJobId)
    setJob({
      status: (targetJob?.status as JobStatus) ?? 'queued',
      message: targetJob?.message ?? 'Loading job...',
      runtimeSessionId: targetJob?.runtimeSessionId,
    })
  }

  const handleRating = useCallback(async (clipIndex: number, rating: 'good' | 'bad') => {
    if (!jobId) return

    setClipFeedback(prev => {
      const old = prev[clipIndex]
      // Clear tags when the rating direction changes (positive tags don't belong on bad ratings)
      const tags = old?.rating === rating ? (old.tags ?? []) : []
      return { ...prev, [clipIndex]: { rating, tags, saving: true, saved: false } }
    })

    try {
      await fetch(`/api/jobs/${jobId}/clips/${clipIndex}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, tags: [] }),
      })
      setClipFeedback(prev => ({
        ...prev,
        [clipIndex]: { ...prev[clipIndex]!, saving: false, saved: true },
      }))
    } catch {
      setClipFeedback(prev => ({
        ...prev,
        [clipIndex]: { ...prev[clipIndex]!, saving: false },
      }))
    }
  }, [jobId])

  const handleTagToggle = useCallback(async (clipIndex: number, tagId: string) => {
    if (!jobId) return

    // Read current state synchronously via the updater — avoids stale closure
    let rating: 'good' | 'bad' | undefined
    let tags: string[] = []

    setClipFeedback(prev => {
      const existing = prev[clipIndex]
      if (!existing?.rating) return prev
      rating = existing.rating
      tags = existing.tags.includes(tagId)
        ? existing.tags.filter(t => t !== tagId)
        : [...existing.tags, tagId]
      return { ...prev, [clipIndex]: { ...existing, tags, saving: true } }
    })

    if (!rating) return

    try {
      await fetch(`/api/jobs/${jobId}/clips/${clipIndex}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, tags }),
      })
      setClipFeedback(prev => ({
        ...prev,
        [clipIndex]: { ...prev[clipIndex]!, saving: false, saved: true },
      }))
    } catch {
      setClipFeedback(prev => ({
        ...prev,
        [clipIndex]: { ...prev[clipIndex]!, saving: false },
      }))
    }
  }, [jobId])

  const loadAnalytics = useCallback(async () => {
    setAnalyticsError(false)
    try {
      const res = await safeFetch('/api/analytics', { timeoutMs: 8000 })
      if (res.ok) {
        setAnalyticsData(await readJsonResponse<AnalyticsInsights>(res))
      } else {
        setAnalyticsError(true)
      }
    } catch {
      setAnalyticsError(true)
    }
  }, [])

  const loadStorageReport = useCallback(async () => {
    setStorageLoading(true)
    try {
      const res = await safeFetch('/api/storage', { timeoutMs: 10000 })
      if (res.ok) {
        setStorageReport(await readJsonResponse<StorageReport>(res))
      }
    } catch {
      // silently fail
    } finally {
      setStorageLoading(false)
    }
  }, [])

  const handleDeleteSourceMedia = useCallback(async () => {
    if (!jobId) return
    setCleanupActionError(null)
    try {
      const res = await safeFetch(`/api/storage/jobs/${jobId}/cleanup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'source_media' }),
        timeoutMs: 15000,
      })
      if (res.ok) {
        setSourceMediaDeleted(true)
        // Mirror deletion in local job state so the compact storage summary
        // doesn't flicker back to "present" on the next poll (server memory
        // is not updated until the next full re-read of result.json).
        setJob(prev => ({
          ...prev,
          result: prev.result ? { ...prev.result, sourceMediaPresent: false } : prev.result,
        }))
        void loadStorageReport()
      } else {
        const data = await readJsonResponse<{ error?: string }>(res).catch(() => ({} as { error?: string }))
        setCleanupActionError(
          res.status === 409
            ? 'Cannot delete: this job is still actively rendering.'
            : (data.error ?? 'Could not delete source video. Try again.')
        )
      }
    } catch {
      setCleanupActionError('Network error. Try again.')
    } finally {
      setCleanupConfirm(null)
    }
  }, [jobId, loadStorageReport])

  const handleDeleteJob = useCallback(async () => {
    if (!jobId) return
    setCleanupActionError(null)
    try {
      const res = await safeFetch(`/api/storage/jobs/${jobId}/cleanup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'job' }),
        timeoutMs: 15000,
      })
      if (res.ok) {
        // Wipe the panel immediately — a deleted job must not linger in the UI.
        resetFlow()
        void loadStorageReport()
      } else {
        const data = await readJsonResponse<{ error?: string }>(res).catch(() => ({} as { error?: string }))
        setCleanupActionError(
          res.status === 409
            ? 'Cannot delete: this job is still actively rendering. Wait for it to finish first.'
            : (data.error ?? 'Could not delete this render. Try again.')
        )
      }
    } catch {
      setCleanupActionError('Network error. Try again.')
    } finally {
      setCleanupConfirm(null)
    }
  }, [jobId, loadStorageReport])

  const handlePruneTemp = useCallback(async () => {
    setPruneWorking(true)
    setLastPruneResult(null)
    try {
      const res = await safeFetch('/api/storage/prune', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pruneTemp: true }),
        timeoutMs: 20000,
      })
      if (res.ok) {
        const data = await readJsonResponse<PruneResult>(res)
        const items = data.temp?.removedItems ?? 0
        const bytes = data.temp?.removedBytes ?? 0
        setLastPruneResult(
          items > 0
            ? `Removed ${items} stale temp item(s), freed ${formatBytes(bytes)}.`
            : 'No stale temp files found.'
        )
        void loadStorageReport()
      }
    } catch {
      setLastPruneResult('Could not prune temp files.')
    } finally {
      setPruneWorking(false)
    }
  }, [loadStorageReport])

  const handlePruneFailed = useCallback(async () => {
    setPruneWorking(true)
    setLastPruneResult(null)
    try {
      const res = await safeFetch('/api/storage/prune', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pruneFailedJobs: true }),
        timeoutMs: 20000,
      })
      if (res.ok) {
        const data = await readJsonResponse<PruneResult>(res)
        const count = data.failedJobs?.removedItems ?? 0
        setLastPruneResult(
          count > 0
            ? `Cleared ${count} failed-job record(s).`
            : 'No failed job records found.'
        )
        void loadStorageReport()
      }
    } catch {
      setLastPruneResult('Could not prune failed job records.')
    } finally {
      setPruneWorking(false)
    }
  }, [loadStorageReport])

  const handleCancelJob = useCallback(async () => {
    if (!jobId) return
    setIsCancelling(true)
    try {
      const res = await safeFetch(`/api/jobs/${jobId}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        timeoutMs: 10000,
      })
      if (res.ok) {
        // Job cancelled - reset to idle state
        resetFlow()
        void loadStorageReport()
      } else {
        const data = await readJsonResponse<{ error?: string }>(res).catch(() => ({} as { error?: string }))
        setRequestError(data.error ?? 'Could not cancel the job.')
      }
    } catch {
      setRequestError('Network error while cancelling.')
    } finally {
      setIsCancelling(false)
    }
  }, [jobId, loadStorageReport])

  function clearSavedApiKey() {
    try {
      window.localStorage.removeItem(apiKeyStorageKey)
    } catch {
      // Ignore storage failures and still clear the field in memory.
    }

    setApiKey('')
    setApiKeyNotice('Saved key cleared from this browser.')
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setRequestError(null)
    setJob({ status: 'queued', message: 'Preparing the local job...' })
    setIsSubmitting(true)

    try {
      const response = await safeFetch('/api/process', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          videoUrl,
          apiKey,
          outputFilename,
          clipCount: selectedClipCount,
          renderProfile: selectedRenderProfile,
          subtitleStyle: lockedSubtitleStyle,
        }),
        timeoutMs: 15000,
      })

      const payload = await readJsonResponse<ProcessErrorPayload>(response)

      if (!response.ok || !payload.jobId) {
        const details = payload.details ? ` ${payload.details}` : ''
        throw new Error((payload.error ?? 'Could not start the job.') + details)
      }

      pollErrorCountRef.current = 0
      setCleanupConfirm(null)
      setSourceMediaDeleted(false)
      setCleanupActionError(null)
      setLastPruneResult(null)
      setJobId(payload.jobId)
      setJob({
        status: payload.status ?? 'queued',
        queuePosition: payload.queuePosition ?? 0,
        renderProfile: payload.renderProfile ?? selectedRenderProfile,
        jobFingerprint: payload.jobFingerprint,
        queueState: payload.queueState,
        waitingOnJobId: payload.waitingOnJobId,
        runtimeSessionId: payload.runtimeSessionId,
        message:
          payload.queueState === 'waiting_for_identical_render'
            ? `This request is waiting for the identical live render${payload.waitingOnJobId ? ` (${payload.waitingOnJobId})` : ''} so the backend can safely reuse the finished output.`
            : (payload.queuePosition ?? 0) > 0
            ? `The job is in queue position ${payload.queuePosition} and will render ${payload.clipCount ?? selectedClipCount} clip(s).`
            : `The job is running locally and will render ${payload.clipCount ?? selectedClipCount} clip(s).`,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unexpected error'
      setJob({ status: 'failed', error: message })
      setRequestError(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  /* ─── Sidebar module: System health ──────────────────────────────── */
  const systemHealthModule = (
    <section className="rounded-lg border border-border bg-white p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">System</h3>
      {doctorReport ? (
        <div className="mt-2 space-y-1.5 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-foreground font-medium">Status</span>
            <span className={`font-semibold ${doctorReport.status === 'FAIL' ? 'text-destructive' : doctorReport.status === 'WARN' ? 'text-amber-600' : 'text-emerald-600'}`}>
              {doctorReport.status}
            </span>
          </div>
          {highlightedStorage.length > 0 ? (
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-muted-foreground">
              {highlightedStorage.map(([key, value]) => (
                <span key={key}>{key}: {formatBytes(value.bytes)}</span>
              ))}
            </div>
          ) : null}
          {highlightedDoctorChecks.length > 0 ? (
            <div className="space-y-0.5 text-muted-foreground">
              {highlightedDoctorChecks.map((check) => (
                <p key={check.name} className={check.status === 'FAIL' ? 'text-destructive' : 'text-amber-600'}>
                  {check.name}: {check.message}
                </p>
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground">Ready for local renders.</p>
          )}
          {doctorReport.status === 'FAIL' ? (
            <p className="mt-1 font-medium text-destructive">
              Rendering is blocked. Fix the checks above.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">Loading checks…</p>
      )}
    </section>
  )

  /* ─── Sidebar module: Runtime ───────────────────────────────────── */
  const runtimeModule = runtimeState ? (
    <section className="rounded-lg border border-border bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Runtime</h3>
        {runtimeState.consistency.status === 'degraded' ? (
          <span className="h-2 w-2 rounded-full bg-destructive" />
        ) : (
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
        )}
      </div>
      <div className="mt-2 space-y-1 text-xs text-muted-foreground">
        <div className="flex justify-between"><span>Active</span><span className="text-foreground font-medium">{runtimeState.queue.activeCount}</span></div>
        <div className="flex justify-between"><span>Queued</span><span className="text-foreground font-medium">{runtimeState.queue.queuedCount}</span></div>
        <div className="flex justify-between"><span>Waiting</span><span className="text-foreground font-medium">{runtimeState.queue.waitingForWorkerCount + runtimeState.queue.waitingForIdenticalRenderCount}</span></div>
        {runtimeRecoverySummary ? (
          <p className="pt-1 text-[11px]">
            Recovery: {runtimeRecoverySummary.clearedLocks} lock(s), {runtimeRecoverySummary.clearedTempWorkspaces} temp, {runtimeRecoverySummary.recoveredJobs} job(s)
          </p>
        ) : null}
        {runtimeIssues.length > 0 ? (
          <div className="pt-1 space-y-0.5 text-destructive">
            {runtimeIssues.map((issue) => <p key={issue}>{issue}</p>)}
          </div>
        ) : null}
      </div>
    </section>
  ) : null

  /* ─── Sidebar module: Jobs ──────────────────────────────────────── */
  const jobsModule = visibleJobs.length > 0 ? (
    <section className="rounded-lg border border-border bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <List className="inline h-3.5 w-3.5 mr-1" />
          Jobs
        </h3>
        <span className="text-[11px] text-muted-foreground">{visibleJobs.length}</span>
      </div>
      <div className="mt-2 space-y-1.5">
        {visibleJobs.map((j) => {
          const isCurrentJob = j.jobId === jobId
          const isActive = ['validating', 'downloading', 'transcribing', 'analyzing', 'rendering'].includes(j.status ?? '')
          const isQueued = j.status === 'queued'
          const isCompleted = j.status === 'completed'
          const isFailed = j.status === 'failed'
          
          return (
            <button
              key={j.jobId}
              type="button"
              onClick={() => !isCurrentJob && switchToJob(j.jobId, { status: j.status, message: j.message, runtimeSessionId: j.runtimeSessionId })}
              disabled={isCurrentJob}
              className={`w-full text-left rounded-md px-2 py-1.5 transition-colors ${
                isCurrentJob
                  ? 'bg-primary/10 ring-1 ring-primary/30'
                  : 'hover:bg-muted'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-foreground truncate max-w-[120px]">
                  {j.jobId.slice(0, 8)}…
                </span>
                <span className={`flex items-center gap-1 text-[10px] font-medium ${
                  isCompleted ? 'text-emerald-600' : 
                  isFailed ? 'text-destructive' : 
                  isActive ? 'text-primary' : 
                  isQueued ? 'text-amber-600' : 
                  'text-muted-foreground'
                }`}>
                  {isActive && <LoaderCircle className="h-2.5 w-2.5 animate-spin" />}
                  {isQueued && <Clock className="h-2.5 w-2.5" />}
                  {isCompleted && <CheckCircle2 className="h-2.5 w-2.5" />}
                  {j.status ?? 'unknown'}
                </span>
              </div>
              {j.overallProgress != null && !isCompleted && !isFailed ? (
                <div className="mt-1 h-1 w-full rounded-full bg-muted overflow-hidden">
                  <div 
                    className={`h-full transition-all ${isActive ? 'bg-primary' : 'bg-amber-500'}`}
                    style={{ width: `${j.overallProgress}%` }}
                  />
                </div>
              ) : null}
              {j.etaSeconds != null && j.etaSeconds > 0 && !isCompleted && !isFailed ? (
                <p className="mt-0.5 text-[10px] text-muted-foreground">
                  ETA: {formatEta(j.etaSeconds)}
                </p>
              ) : null}
            </button>
          )
        })}
      </div>
    </section>
  ) : null

  /* ─── Sidebar module: Storage ───────────────────────────────────── */
  const storageModule = (
    <section className="rounded-lg border border-border bg-white p-4">
      <button
        type="button"
        onClick={() => {
          setShowStorage(prev => !prev)
          if (!storageReport) void loadStorageReport()
        }}
        className="flex w-full items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground transition-colors"
      >
        <span className="flex items-center gap-1.5">
          <HardDrive className="h-3.5 w-3.5" />
          Storage
        </span>
        <span className="flex items-center gap-1.5 font-normal normal-case">
          {storageReport ? formatBytes(
            (storageReport.summary.jobs.bytes ?? 0) +
            (storageReport.summary.cache.bytes ?? 0) +
            (storageReport.summary.temp.bytes ?? 0)
          ) : null}
          {storageLoading ? <RefreshCw className="h-3 w-3 animate-spin" /> : null}
          <span>{showStorage ? '▲' : '▼'}</span>
        </span>
      </button>

      {showStorage ? (
        <div className="mt-3 space-y-2.5">
          {storageReport ? (
            <>
              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between text-muted-foreground">
                  <span>Renders</span>
                  <span className="text-foreground font-medium">{formatBytes(storageReport.summary.jobs.bytes)}</span>
                </div>
                <div className="flex justify-between text-muted-foreground">
                  <span>Cache</span>
                  <span className="text-foreground font-medium">{formatBytes(storageReport.summary.cache.bytes)}</span>
                </div>
                <div className="flex justify-between text-muted-foreground">
                  <span>Temp</span>
                  <span className={`font-medium ${storageReport.summary.temp.bytes > 0 ? 'text-amber-600' : 'text-foreground'}`}>
                    {formatBytes(storageReport.summary.temp.bytes)}
                  </span>
                </div>
                <div className="flex justify-between text-muted-foreground pt-0.5 border-t border-border">
                  <span>Jobs</span>
                  <span className="text-foreground">{storageReport.jobStateCounts.completed} done · {storageReport.jobStateCounts.failed} failed</span>
                </div>
              </div>

              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  disabled={pruneWorking || !storageReport.recommendations.canPruneTemp}
                  onClick={() => void handlePruneTemp()}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-border hover:bg-muted disabled:opacity-40 transition-colors"
                >
                  {pruneWorking ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  Prune temp
                </button>
                <button
                  type="button"
                  disabled={pruneWorking || storageReport.jobStateCounts.failed === 0}
                  onClick={() => void handlePruneFailed()}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-border hover:bg-muted disabled:opacity-40 transition-colors"
                >
                  {pruneWorking ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  Clear failed ({storageReport.jobStateCounts.failed})
                </button>
                <button
                  type="button"
                  onClick={() => void loadStorageReport()}
                  disabled={storageLoading}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-border hover:bg-muted disabled:opacity-40 transition-colors"
                >
                  <RefreshCw className={`h-3 w-3 ${storageLoading ? 'animate-spin' : ''}`} />
                </button>
              </div>

              {lastPruneResult ? (
                <p className="text-[11px] text-emerald-600">{lastPruneResult}</p>
              ) : null}
            </>
          ) : (
            <p className="text-xs text-muted-foreground">{storageLoading ? 'Loading…' : 'Could not load storage usage.'}</p>
          )}
        </div>
      ) : null}
    </section>
  )

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      {/* ─── App bar ──────────────────────────────────────────────── */}
      <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center justify-between border-b border-border bg-white/80 px-4 backdrop-blur-sm sm:px-6">
        <div className="flex items-center gap-2.5">
          <span className="text-sm font-semibold tracking-tight text-foreground">Shorts Studio</span>
          <span className="text-xs text-muted-foreground">Local</span>
        </div>
        <div className="flex items-center gap-3">
          {/* Health dot */}
          {doctorReport ? (
            <span className={`h-2 w-2 rounded-full ${doctorReport.status === 'FAIL' ? 'bg-destructive' : doctorReport.status === 'WARN' ? 'bg-amber-500' : 'bg-emerald-500'}`} title={`System: ${doctorReport.status}`} />
          ) : null}
          {hasStarted ? (
            <button type="button" onClick={resetFlow} className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border hover:bg-muted transition-colors">
              <RotateCcw className="h-3 w-3" /> New job
            </button>
          ) : null}
        </div>
      </header>

      {/* ─── Main workspace ───────────────────────────────────────── */}
      <div className="mx-auto flex w-full max-w-6xl flex-1 gap-5 p-4 sm:p-6 lg:flex-row flex-col">

        {/* ─── Main column ────────────────────────────────────────── */}
        <main className="min-w-0 flex-1 space-y-4">
          {!hasStarted ? (
            /* ━━━━ IDLE / FORM STATE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
            <form className="space-y-3" onSubmit={handleSubmit}>
              {/* Compose section */}
              <section className="rounded-lg border border-border bg-white p-4 space-y-3.5">
                <div>
                  <h2 className="text-sm font-semibold text-foreground">New render</h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">Paste a YouTube link and configure the output.</p>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="videoUrl">YouTube URL</Label>
                  <Input
                    id="videoUrl"
                    value={videoUrl}
                    onChange={(event) => setVideoUrl(event.target.value)}
                    placeholder="https://www.youtube.com/watch?v=..."
                    autoComplete="off"
                    required
                  />
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="apiKey">Gemini API key</Label>
                    <button type="button" onClick={clearSavedApiKey} className="text-[11px] text-muted-foreground hover:text-foreground transition-colors">
                      Clear saved
                    </button>
                  </div>
                  <Input
                    id="apiKey"
                    type="password"
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder={hasConfiguredApiKey ? 'Using server .env key' : 'Paste your Gemini key'}
                    autoComplete="off"
                  />
                  <p className="text-[11px] text-muted-foreground">
                    {hasConfiguredApiKey
                      ? 'Server key detected. Paste another to override for this session.'
                      : apiKey.trim()
                        ? 'Key stored in this browser session.'
                        : apiKeyNotice}
                  </p>
                </div>
              </section>

              {/* Render settings section */}
              <section className="rounded-lg border border-border bg-white p-4 space-y-3.5">
                <div>
                  <h2 className="text-sm font-semibold text-foreground">Settings</h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Speaker mode: {speakerMode === 'auto' ? 'auto' : speakerMode} · Subtitles: editorial
                  </p>
                </div>

                <div className="space-y-1.5">
                  <Label>Clips</Label>
                  <div className="flex gap-1.5">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        type="button"
                        onClick={() => setSelectedClipCount(n)}
                        className={`flex h-8 w-8 items-center justify-center rounded-md text-xs font-medium transition-colors ${
                          selectedClipCount === n
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted text-muted-foreground hover:bg-muted/80'
                        }`}
                      >
                        {n}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label>Profile</Label>
                  <div className="grid gap-1.5 sm:grid-cols-3">
                    {Object.entries(renderProfiles).map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setSelectedRenderProfile(key)}
                        className={`rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                          selectedRenderProfile === key
                            ? 'border-primary bg-primary text-primary-foreground'
                            : 'border-border bg-white text-foreground hover:border-primary/30 hover:bg-muted'
                        }`}
                      >
                        <span className="block font-medium">{label}</span>
                        <span className={`mt-0.5 block text-[10px] ${selectedRenderProfile === key ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>
                          {key === 'fast' ? 'Quick iteration' : key === 'balanced' ? 'Daily default' : 'Highest quality'}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Time estimate preview */}
                <div className="rounded-md border border-border bg-muted/50 px-3 py-2">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Clock className="h-3.5 w-3.5" />
                    <span>
                      Estimated time: <span className="font-medium text-foreground">{preStartEstimate[0]}–{preStartEstimate[1]} min</span>
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Depends on video length and network speed. Transcription is usually the longest step.
                  </p>
                </div>

                <Button className="w-full" type="submit" disabled={!canSubmit}>
                  {isSubmitting || isWorking ? (
                    <><LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> Rendering…</>
                  ) : (
                    <><PlaySquare className="mr-2 h-3.5 w-3.5" /> Start render</>
                  )}
                </Button>

                {!hasAvailableApiKey ? (
                  <p className="text-xs text-amber-600">Add a Gemini key above or set GEMINI_API_KEY in .env.</p>
                ) : null}
                {requestError ? <p className="text-xs text-destructive">{requestError}</p> : null}
                {hasBlockingDoctorFailure ? (
                  <p className="text-xs text-destructive">Rendering is blocked by system check failures.</p>
                ) : null}
              </section>
            </form>
          ) : (
            /* ━━━━ ACTIVE JOB / COMPLETED STATE ━━━━━━━━━━━━━━━━━━━━ */
            <div className="space-y-4">
              {/* Status header */}
              <section className="rounded-lg border border-border bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-semibold text-foreground">{statusTitles[job.status]}</h2>
                      <Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'}>{job.status}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{job.message ?? stageDescriptions[job.status]}</p>
                    {job.stageProgress != null ? (
                      <p className="mt-0.5 text-[11px] text-muted-foreground">Stage progress: {job.stageProgress}%</p>
                    ) : null}
                  </div>
                  {/* Cancel button - only show when job is in progress (not completed/failed) */}
                  {!['completed', 'failed', 'idle'].includes(job.status) ? (
                    <button
                      type="button"
                      onClick={() => void handleCancelJob()}
                      disabled={isCancelling}
                      className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-destructive ring-1 ring-destructive/30 hover:bg-destructive/10 disabled:opacity-50 transition-colors shrink-0"
                    >
                      {isCancelling ? (
                        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <StopCircle className="h-3.5 w-3.5" />
                      )}
                      {isCancelling ? 'Cancelling…' : 'Cancel'}
                    </button>
                  ) : null}
                </div>
                <div className="mt-3 space-y-1">
                  <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                    <span>Progress</span>
                    <span>{progressValue}%</span>
                  </div>
                  <Progress value={progressValue} />
                </div>
                {etaLabel ? (
                  <p className="mt-2 text-xs text-muted-foreground">ETA: {etaLabel}</p>
                ) : null}
              </section>

              {/* Reconnecting notice */}
              {isReconnecting ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 flex items-center gap-2">
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin shrink-0" />
                  Reconnecting to backend…
                </div>
              ) : null}

              {/* Queue notices */}
              {job.status === 'queued' && (job.queuePosition ?? 0) > 0 ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Queue position: {job.queuePosition}
                </div>
              ) : null}

              {job.status === 'queued' && job.queueState === 'waiting_for_identical_render' ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Waiting on identical render{job.waitingOnJobId ? `: ${job.waitingOnJobId}` : ''}
                </div>
              ) : null}

              {/* Pipeline stages */}
              <section className="rounded-lg border border-border bg-white p-4">
                <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
                  {Object.entries(statusTitles)
                    .filter(([status]) => status !== 'idle' && status !== 'failed')
                    .map(([status, title]) => {
                      const active = progressByStatus[job.status] >= progressByStatus[status as JobStatus]
                      return (
                        <div key={status} className={`rounded-md px-2 py-1.5 text-center text-[10px] font-medium ${active ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>
                          {title.replace('Under ', '').replace('Ready for ', '')}
                        </div>
                      )
                    })}
                </div>
              </section>

              {/* Job detail */}
              <section className="rounded-lg border border-border bg-white p-4 text-xs text-muted-foreground space-y-1">
                <p>Clips: {effectiveClipCount} · Profile: {job.result?.renderProfile ?? currentRenderProfileLabel}</p>
                {job.result?.reusedExisting ? <p className="text-emerald-600">Reused existing render.</p> : null}
              </section>

              {/* Error */}
              {job.error ? (
                <section className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-xs text-destructive space-y-1">
                  <p className="font-medium">{job.error}</p>
                  {job.errorHelp ? <p>{job.errorHelp}</p> : null}
                  {job.errorId ? <p>Support ID: {job.errorId}</p> : null}
                </section>
              ) : null}

              {/* Live log */}
              <section className="rounded-lg border border-border bg-white p-4">
                <h3 className="text-xs font-semibold text-foreground">Activity</h3>
                {recentLogs.length > 0 ? (
                  <div className="mt-2 space-y-1">
                    {recentLogs.map((entry, index) => (
                      <div key={`${entry.time}-${index}`} className="flex items-start gap-2 text-[11px]">
                        <span className="shrink-0 font-mono text-muted-foreground">{formatLogTime(entry.time)}</span>
                        <span className="shrink-0 uppercase tracking-wider text-muted-foreground/60 text-[10px]">{entry.stage}</span>
                        <span className="text-foreground">{entry.message}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-muted-foreground">Waiting for backend activity…</p>
                )}
              </section>

              {/* ━━━ Output / Downloads ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
              <section className="rounded-lg border border-border bg-white p-4 space-y-3">
                <h3 className="text-sm font-semibold text-foreground">Output</h3>

                {job.status === 'completed' && job.result && jobId ? (
                  <>
                    {/* Completion banner */}
                    <div className="flex items-center gap-2 rounded-md bg-emerald-50 border border-emerald-200 px-3 py-2 text-xs text-emerald-800">
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                      <span><strong>{job.result.clipCount} clip(s)</strong> ready · {job.result.renderProfile ?? currentRenderProfileLabel}</span>
                      {job.result.reusedExisting ? <span className="text-emerald-600"> · reused</span> : null}
                    </div>

                    {/* Run stats (compact) */}
                    {job.result.runSummary ? (
                      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs text-muted-foreground sm:grid-cols-4">
                        <span>Time: {job.result.runSummary.totalJobSeconds ?? '?'}s</span>
                        <span>Generated: {job.result.runSummary.generatedClipCount ?? 0}</span>
                        {job.result.runSummary.finalOutputBytes ? <span>Size: {formatBytes(Number(job.result.runSummary.finalOutputBytes))}</span> : null}
                        {job.result.runSummary.peakRssBytes || job.result.runSummary.peakProcessRssBytes ? (
                          <span>Mem: {formatBytes(Number(job.result.runSummary.peakRssBytes ?? job.result.runSummary.peakProcessRssBytes ?? 0))}</span>
                        ) : null}
                      </div>
                    ) : null}

                    {/* Clip list */}
                    <div className="space-y-2.5">
                      {job.result.clips.map((clip) => {
                        const fb = clipFeedback[clip.index]
                        return (
                          <div key={clip.index} className="rounded-md border border-border p-3 space-y-2">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <p className="text-xs font-semibold text-foreground">{clip.title ?? `Clip ${clip.index + 1}`}</p>
                                <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{clip.reason ?? 'Selected by Gemini.'}</p>
                                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                                  <span>{clip.start.toFixed(1)}s – {clip.end.toFixed(1)}s</span>
                                  {clip.contentType ? <Badge variant="outline" className="text-[11px] px-1.5 py-0">{clip.contentType}</Badge> : null}
                                  {clip.analytics?.speakerCountEstimate != null ? <span>{String(clip.analytics.speakerCountEstimate)} speaker{clip.analytics.speakerCountEstimate === 1 ? '' : 's'}</span> : null}
                                </div>
                              </div>
                              <div className="flex shrink-0 gap-1.5">
                                <button
                                  type="button"
                                  onClick={() => setPreviewClipIndex(prev => prev === clip.index ? null : clip.index)}
                                  className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                                    previewClipIndex === clip.index
                                      ? 'bg-primary text-primary-foreground'
                                      : 'text-muted-foreground ring-1 ring-border hover:bg-muted'
                                  }`}
                                >
                                  <Play className="h-3 w-3" /> {previewClipIndex === clip.index ? 'Close' : 'Preview'}
                                </button>
                                <Button asChild size="sm" className="shrink-0">
                                  <a href={`/api/jobs/${jobId}/download/video/${clip.index}`}>
                                    <Download className="mr-1.5 h-3 w-3" /> Download
                                  </a>
                                </Button>
                              </div>
                            </div>

                            {/* Inline video preview */}
                            {previewClipIndex === clip.index ? (
                              <div className="mt-2.5 overflow-hidden rounded-md border border-border bg-black">
                                <video
                                  key={`preview-${clip.index}`}
                                  src={`/api/jobs/${jobId}/preview/video/${clip.index}`}
                                  controls
                                  autoPlay
                                  playsInline
                                  className="mx-auto max-h-[420px] w-auto"
                                />
                              </div>
                            ) : null}

                            {/* Feedback */}
                            <div className="flex items-center gap-2 border-t border-border pt-2">
                              <span className="text-xs text-muted-foreground">Rate:</span>
                              <button
                                type="button"
                                onClick={() => void handleRating(clip.index, 'good')}
                                className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium transition-colors ${
                                  fb?.rating === 'good' ? 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200' : 'bg-muted text-muted-foreground hover:bg-emerald-50'
                                }`}
                              ><ThumbsUp className="h-3 w-3" /> Good</button>
                              <button
                                type="button"
                                onClick={() => void handleRating(clip.index, 'bad')}
                                className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium transition-colors ${
                                  fb?.rating === 'bad' ? 'bg-red-100 text-red-700 ring-1 ring-red-200' : 'bg-muted text-muted-foreground hover:bg-red-50'
                                }`}
                              ><ThumbsDown className="h-3 w-3" /> Bad</button>
                              {fb?.saved ? <span className="text-xs text-emerald-600">Saved</span> : null}
                            </div>

                            {fb?.rating ? (
                              <div className="mt-1.5 flex flex-wrap gap-1.5">
                                {feedbackTags
                                  .filter(t => fb.rating === 'good' ? t.positive : !t.positive)
                                  .map(tag => (
                                    <button
                                      key={tag.id}
                                      type="button"
                                      onClick={() => void handleTagToggle(clip.index, tag.id)}
                                      className={`rounded-md px-2 py-0.5 text-xs font-medium transition-colors ${
                                        fb.tags.includes(tag.id)
                                          ? fb.rating === 'good'
                                            ? 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200'
                                            : 'bg-red-100 text-red-700 ring-1 ring-red-200'
                                          : 'bg-muted text-muted-foreground hover:bg-muted/80'
                                      }`}
                                    >
                                      {tag.label}
                                    </button>
                                  ))}
                              </div>
                            ) : null}
                          </div>
                        )
                      })}
                    </div>

                    {/* Transcript download */}
                    <Button asChild variant="outline" size="sm" className="w-full">
                      <a href={`/api/jobs/${jobId}/download/transcript`}>
                        <Download className="mr-1.5 h-3 w-3" /> Download transcript
                      </a>
                    </Button>

                    {/* ─── Disk management ─────────────────────────── */}
                    <div className="border-t border-border pt-3 space-y-2.5">
                      <div>
                        <h4 className="text-xs font-semibold text-foreground">Disk</h4>
                        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                          {sourceMediaDeleted || job.result.sourceMediaPresent === false ? (
                            <span className="flex items-center gap-1 text-emerald-600"><CheckCircle2 className="h-3 w-3" /> Source deleted</span>
                          ) : (
                            <span className="flex items-center gap-1"><HardDrive className="h-3 w-3 text-amber-500" /> Source on disk</span>
                          )}
                          <span>
                            {job.result.clipCount} clip(s)
                            {job.result.runSummary?.finalOutputBytes ? ` · ${formatBytes(Number(job.result.runSummary.finalOutputBytes))}` : ''}
                          </span>
                        </div>
                      </div>

                      {/* Delete source */}
                      {sourceMediaDeleted || job.result?.sourceMediaPresent === false ? (
                        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                          <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-500" /> Source video already deleted
                        </div>
                      ) : cleanupConfirm === 'source' ? (
                        <div className="rounded-md border border-amber-200 bg-amber-50 p-2.5 space-y-1.5">
                          <p className="text-[11px] font-semibold text-amber-800">Delete source video?</p>
                          <p className="text-[11px] text-amber-700">Clips, transcript, and metadata are kept.</p>
                          <div className="flex gap-1.5">
                            <button type="button" onClick={() => void handleDeleteSourceMedia()} className="rounded-md bg-amber-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-amber-700 transition-colors">
                              <Trash2 className="mr-1 inline h-3 w-3" /> Delete
                            </button>
                            <button type="button" onClick={() => setCleanupConfirm(null)} className="rounded-md px-2 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-border hover:bg-muted transition-colors">
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-center justify-between">
                          <span className="text-[11px] text-muted-foreground">Delete source video (keeps clips)</span>
                          <button type="button" onClick={() => setCleanupConfirm('source')} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200 hover:bg-amber-50 transition-colors">
                            <Trash2 className="h-3 w-3" /> Delete source
                          </button>
                        </div>
                      )}

                      {/* Delete entire job */}
                      {cleanupConfirm === 'job' ? (
                        <div className="rounded-md border border-red-200 bg-red-50 p-2.5 space-y-1.5">
                          <p className="text-[11px] font-semibold text-red-800">Delete everything?</p>
                          <p className="text-[11px] text-red-700">Download clips first — all files will be permanently removed.</p>
                          <div className="flex gap-1.5">
                            <button type="button" onClick={() => void handleDeleteJob()} className="rounded-md bg-red-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-red-700 transition-colors">
                              <Trash2 className="mr-1 inline h-3 w-3" /> Delete all
                            </button>
                            <button type="button" onClick={() => setCleanupConfirm(null)} className="rounded-md px-2 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-border hover:bg-muted transition-colors">
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-center justify-between">
                          <span className="text-[11px] text-muted-foreground">Delete entire render</span>
                          <button type="button" onClick={() => setCleanupConfirm('job')} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-red-600 ring-1 ring-red-200 hover:bg-red-50 transition-colors">
                            <Trash2 className="h-3 w-3" /> Delete all
                          </button>
                        </div>
                      )}

                      {cleanupActionError ? (
                        <p className="rounded-md border border-destructive/20 bg-destructive/5 px-2 py-1.5 text-[11px] text-destructive">{cleanupActionError}</p>
                      ) : null}
                    </div>

                    {/* Analytics */}
                    <div className="border-t border-border pt-3">
                      <button
                        type="button"
                        onClick={() => {
                          setShowAnalytics(prev => !prev)
                          if (!analyticsData) void loadAnalytics()
                        }}
                        className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
                      >
                        <BarChart3 className="h-3.5 w-3.5" />
                        {showAnalytics ? 'Hide' : 'Show'} insights
                      </button>

                      {showAnalytics && analyticsData ? (
                        <div className="mt-2 space-y-2">
                          <div className="grid grid-cols-4 gap-1.5 text-center text-xs">
                            <div className="rounded-md bg-muted p-2">
                              <p className="text-base font-bold text-foreground">{analyticsData.totalClips}</p>
                              <p className="text-[10px] text-muted-foreground">Total</p>
                            </div>
                            <div className="rounded-md bg-muted p-2">
                              <p className="text-base font-bold text-foreground">{analyticsData.totalRated}</p>
                              <p className="text-[10px] text-muted-foreground">Rated</p>
                            </div>
                            <div className="rounded-md bg-emerald-50 p-2">
                              <p className="text-base font-bold text-emerald-700">{analyticsData.totalGood}</p>
                              <p className="text-[10px] text-emerald-600">Good</p>
                            </div>
                            <div className="rounded-md bg-red-50 p-2">
                              <p className="text-base font-bold text-red-700">{analyticsData.totalBad}</p>
                              <p className="text-[10px] text-red-600">Bad</p>
                            </div>
                          </div>

                          {analyticsData.overallApprovalRate != null ? (
                            <p className="text-xs text-muted-foreground">
                              Approval: <span className="font-semibold text-foreground">{(analyticsData.overallApprovalRate * 100).toFixed(0)}%</span> across {analyticsData.totalRated} rated
                            </p>
                          ) : null}

                          {Object.keys(analyticsData.perContentType).length > 0 ? (
                            <div className="space-y-1">
                              {Object.entries(analyticsData.perContentType).map(([ct, stats]) => (
                                <div key={ct} className="flex items-center justify-between text-[11px] text-muted-foreground">
                                  <span className="font-medium text-foreground">{ct}</span>
                                  <div className="flex items-center gap-2">
                                    <span>{stats.clipCount} clips</span>
                                    {stats.approvalRate != null ? (
                                      <span className={stats.approvalRate >= 0.7 ? 'text-emerald-600' : stats.approvalRate < 0.5 ? 'text-red-600' : 'text-amber-600'}>
                                        {(stats.approvalRate * 100).toFixed(0)}%
                                      </span>
                                    ) : null}
                                  </div>
                                </div>
                              ))}
                            </div>
                          ) : null}

                          <button type="button" onClick={() => void loadAnalytics()} className="text-[11px] text-primary hover:text-primary/80 transition-colors">
                            Refresh
                          </button>
                        </div>
                      ) : showAnalytics ? (
                        <p className="mt-2 text-xs text-muted-foreground">
                          {analyticsError ? 'Could not load insights. ' : 'Loading…'}
                          {analyticsError && (
                            <button type="button" onClick={() => void loadAnalytics()} className="text-primary hover:text-primary/80 transition-colors">
                              Retry
                            </button>
                          )}
                        </p>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <div className="flex flex-col items-center justify-center py-6 text-center">
                    <p className="text-sm font-medium text-foreground">Clips will appear here when ready</p>
                    <p className="mt-1 text-xs text-muted-foreground">Up to {effectiveClipCount} clip(s) · 1080×1920 MP4 · transcript included</p>
                  </div>
                )}
              </section>
            </div>
          )}
        </main>

        {/* ─── Sidebar ────────────────────────────────────────────── */}
        <aside className="w-full shrink-0 space-y-3 lg:w-64">
          {systemHealthModule}
          {runtimeModule}
          {jobsModule}
          {storageModule}
        </aside>
      </div>
    </div>
  )
}

export default App
