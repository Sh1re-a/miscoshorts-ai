import { useCallback, useEffect, useEffectEvent, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, Download, LoaderCircle, PlaySquare, RotateCcw, ThumbsUp, ThumbsDown, BarChart3 } from 'lucide-react'

import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui/card'
import { Input } from './components/ui/input'
import { Label } from './components/ui/label'
import { Progress } from './components/ui/progress'
import { feedbackTags, progressByStatus, stageDescriptions, statusTitles } from './features/jobs/config'
import type { AnalyticsInsights, BootstrapPayload, ClipFeedback, JobPayload, JobStatus } from './features/jobs/types'
import { apiKeyStorageKey, formatEta, formatLogTime, getEtaWindow, loadSavedApiKey } from './features/jobs/utils'
import type { SubtitlePreviewPayload } from './features/preview/types'

const fallbackRenderProfile = 'studio'
const fallbackRenderProfiles = {
  fast: 'Fast Draft 1080x1920 MP4',
  balanced: 'Balanced 1080x1920 MP4',
  studio: 'Studio HQ 1080x1920 MP4',
}
const fontPresetOptions = ['soft', 'clean', 'bold'] as const
const colorPresetOptions = ['editorial', 'ivory', 'mint', 'sun'] as const

function App() {
  const [videoUrl, setVideoUrl] = useState('')
  const [apiKey, setApiKey] = useState(loadSavedApiKey)
  const [hasConfiguredApiKey, setHasConfiguredApiKey] = useState(false)
  const [renderProfiles, setRenderProfiles] = useState<Record<string, string>>(fallbackRenderProfiles)
  const [selectedRenderProfile, setSelectedRenderProfile] = useState(fallbackRenderProfile)
  const [speakerMode, setSpeakerMode] = useState('auto')
  const [hasPyannoteToken, setHasPyannoteToken] = useState(false)
  const [selectedFontPreset, setSelectedFontPreset] = useState<'soft' | 'clean' | 'bold'>('soft')
  const [selectedColorPreset, setSelectedColorPreset] = useState<'editorial' | 'ivory' | 'mint' | 'sun'>('editorial')
  const [previewTitle, setPreviewTitle] = useState('A calm, premium headline')
  const [previewReason, setPreviewReason] = useState('Subtle captions, refined hierarchy, and a more editorial finish.')
  const [previewData, setPreviewData] = useState<SubtitlePreviewPayload | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [outputFilename, setOutputFilename] = useState('short_con_subs.mp4')
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobPayload>({ status: 'idle' })
  const [requestError, setRequestError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [apiKeyNotice, setApiKeyNotice] = useState(apiKey ? 'Saved locally in this browser.' : 'Not saved yet.')
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [clipFeedback, setClipFeedback] = useState<Record<number, ClipFeedback>>({})
  const [analyticsData, setAnalyticsData] = useState<AnalyticsInsights | null>(null)
  const [showAnalytics, setShowAnalytics] = useState(false)
  const [selectedClipCount, setSelectedClipCount] = useState(3)

  useEffect(() => {
    let cancelled = false

    async function loadBootstrap() {
      try {
        const response = await fetch('/api/bootstrap')
        if (!response.ok) {
          return
        }

        const payload = (await response.json()) as BootstrapPayload
        if (!cancelled) {
          setHasConfiguredApiKey(payload.hasConfiguredApiKey)
          setRenderProfiles(payload.renderProfiles)
          setSelectedRenderProfile(payload.defaultRenderProfile || fallbackRenderProfile)
          setSpeakerMode(payload.speakerDiarizationMode)
          setHasPyannoteToken(payload.hasPyannoteToken)
        }
      } catch {
        // Keep the app usable even if the bootstrap request fails.
      }
    }

    void loadBootstrap()

    return () => {
      cancelled = true
    }
  }, [])

  const pollJob = useEffectEvent(async () => {
    if (!jobId) {
      return
    }

    const response = await fetch(`/api/jobs/${jobId}`)
    const payload = (await response.json()) as JobPayload

    if (!response.ok) {
      throw new Error(payload.error ?? 'Could not load job status.')
    }

    setJob(payload)
  })

  useEffect(() => {
    if (!jobId) {
      return
    }

    void pollJob()

    const intervalId = window.setInterval(() => {
      void pollJob()
    }, 1200)

    return () => window.clearInterval(intervalId)
  }, [jobId, pollJob])

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
  const canSubmit = Boolean(videoUrl.trim()) && hasAvailableApiKey && !isSubmitting && !isWorking
  const startButtonLabel = isSubmitting || isWorking ? 'Job running' : 'Start studio render'
  const etaWindow = useMemo(() => getEtaWindow(job, selectedClipCount, nowMs), [job, selectedClipCount, nowMs])
  const etaLabel = job.etaSeconds != null
    ? formatEta(job.etaSeconds)
    : etaWindow
      ? `${formatEta(etaWindow[0])} to ${formatEta(etaWindow[1])}`
      : null
  const hasStarted = job.status !== 'idle' || isSubmitting || jobId !== null
  const effectiveClipCount = job.result?.clipCount ?? job.clipCount ?? selectedClipCount
  const currentRenderProfileLabel = renderProfiles[selectedRenderProfile] ?? renderProfiles[fallbackRenderProfile] ?? 'Studio HQ 1080x1920 MP4'
  const progressValue = job.overallProgress ?? progressByStatus[job.status]
  const selectedSubtitleStyle = useMemo(() => ({ fontPreset: selectedFontPreset, colorPreset: selectedColorPreset }), [selectedColorPreset, selectedFontPreset])

  useEffect(() => {
    let cancelled = false
    const timeoutId = window.setTimeout(async () => {
      setPreviewLoading(true)
      try {
        const response = await fetch('/api/subtitle-preview', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            subtitleStyle: selectedSubtitleStyle,
            title: previewTitle,
            reason: previewReason,
          }),
        })
        if (!response.ok) {
          return
        }
        const payload = await response.json() as SubtitlePreviewPayload
        if (!cancelled) {
          setPreviewData(payload)
        }
      } catch {
        // Keep the app usable if preview generation fails.
      } finally {
        if (!cancelled) {
          setPreviewLoading(false)
        }
      }
    }, 250)

    return () => {
      cancelled = true
      window.clearTimeout(timeoutId)
    }
  }, [previewReason, previewTitle, selectedSubtitleStyle])

  function resetFlow() {
    setJobId(null)
    setJob({ status: 'idle' })
    setRequestError(null)
    setIsSubmitting(false)
    setOutputFilename('short_con_subs.mp4')
    setClipFeedback({})
  }

  const handleRating = useCallback(async (clipIndex: number, rating: 'good' | 'bad') => {
    if (!jobId) return

    setClipFeedback(prev => ({
      ...prev,
      [clipIndex]: { ...prev[clipIndex], rating, tags: prev[clipIndex]?.tags ?? [], saving: true, saved: false },
    }))

    try {
      const existing = clipFeedback[clipIndex]
      await fetch(`/api/jobs/${jobId}/clips/${clipIndex}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, tags: existing?.tags ?? [] }),
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
  }, [jobId, clipFeedback])

  const handleTagToggle = useCallback(async (clipIndex: number, tagId: string) => {
    if (!jobId) return

    const existing = clipFeedback[clipIndex]
    if (!existing?.rating) return

    const tags = existing.tags.includes(tagId)
      ? existing.tags.filter(t => t !== tagId)
      : [...existing.tags, tagId]

    setClipFeedback(prev => ({
      ...prev,
      [clipIndex]: { ...prev[clipIndex]!, tags, saving: true },
    }))

    try {
      await fetch(`/api/jobs/${jobId}/clips/${clipIndex}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating: existing.rating, tags }),
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
  }, [jobId, clipFeedback])

  const loadAnalytics = useCallback(async () => {
    try {
      const res = await fetch('/api/analytics')
      if (res.ok) {
        setAnalyticsData(await res.json() as AnalyticsInsights)
      }
    } catch {
      // silently fail
    }
  }, [])

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
      const response = await fetch('/api/process', {
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
          subtitleStyle: selectedSubtitleStyle,
        }),
      })

      const payload = (await response.json()) as { jobId?: string; error?: string; status?: JobStatus; clipCount?: number; queuePosition?: number; renderProfile?: string }

      if (!response.ok || !payload.jobId) {
        throw new Error(payload.error ?? 'Could not start the job.')
      }

      setJobId(payload.jobId)
      setJob({
        status: payload.status ?? 'queued',
        queuePosition: payload.queuePosition ?? 0,
        renderProfile: payload.renderProfile ?? selectedRenderProfile,
        message:
          (payload.queuePosition ?? 0) > 0
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

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(180,220,255,0.35),_transparent_28%),linear-gradient(180deg,_#f6fbff_0%,_#eef7ff_42%,_#f7fbff_100%)] text-slate-900">
      <div className="mx-auto flex min-h-screen max-w-3xl items-center justify-center px-4 py-8 sm:px-6 lg:px-8">
        <Card className="w-full overflow-hidden rounded-[34px] border border-sky-100 bg-white shadow-[0_30px_80px_rgba(66,124,184,0.14)]">
          <CardHeader className="border-b border-sky-100/90 bg-[linear-gradient(180deg,_rgba(247,251,255,0.98)_0%,_rgba(238,247,255,0.98)_100%)] p-6 sm:p-8">
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-3">
                <Badge variant="outline" className="border-sky-200 bg-sky-50 text-sky-700">
                  Local Shorts Studio
                </Badge>
                <div className="space-y-2">
                  <CardTitle className="app-heading text-3xl text-slate-950 sm:text-4xl">Create clean clips from one link</CardTitle>
                  <CardDescription className="max-w-xl text-sm leading-6 text-slate-600 sm:text-base">
                    Paste your YouTube link, add a Gemini key, and let the app handle the full Shorts workflow for you.
                  </CardDescription>
                </div>
              </div>

              {hasStarted ? (
                <Button type="button" variant="secondary" className="shrink-0 bg-white text-slate-700 hover:bg-sky-50" onClick={resetFlow}>
                  <RotateCcw className="mr-2 h-4 w-4" /> New job
                </Button>
              ) : null}
            </div>
          </CardHeader>

          <CardContent className="space-y-6 p-6 sm:p-8">
            {!hasStarted ? (
              <form className="space-y-5" onSubmit={handleSubmit}>
                <div className="space-y-2">
                  <Label htmlFor="videoUrl">YouTube URL</Label>
                  <Input
                    id="videoUrl"
                    value={videoUrl}
                    onChange={(event) => setVideoUrl(event.target.value)}
                    placeholder="https://www.youtube.com/watch?v=..."
                    autoComplete="off"
                    required
                  />
                  <p className="text-xs text-slate-500">Paste the full YouTube video link you want to turn into Shorts. The app handles the rest.</p>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <Label htmlFor="apiKey">Gemini API key</Label>
                    <button
                      type="button"
                      onClick={clearSavedApiKey}
                      className="text-xs font-medium text-slate-500 transition hover:text-slate-900"
                    >
                      Clear saved key
                    </button>
                  </div>
                  <Input
                    id="apiKey"
                    type="password"
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder={hasConfiguredApiKey ? 'Optional if GEMINI_API_KEY is already set in .env' : 'Paste your Gemini key'}
                    autoComplete="off"
                  />
                  <p className="text-xs text-slate-500">
                    {hasConfiguredApiKey
                      ? 'A Gemini key is already configured on this machine. You can still paste another key here for this browser session.'
                      : apiKeyNotice}
                  </p>
                </div>

                <div className="rounded-[24px] border border-sky-100 bg-sky-50/80 px-4 py-4 text-sm leading-6 text-slate-700">
                  <p className="font-semibold text-slate-900">{currentRenderProfileLabel}</p>
                  <p className="mt-2">The app runs a focused Shorts workflow: centered reframing, strong H.264 export settings, AAC audio, and calmer editorial subtitles with a more premium header.</p>
                  <p className="mt-2">Keep the launcher window open while the job runs. Finished files appear here and in the local outputs folder.</p>
                  <p className="mt-2 text-slate-500">
                    Speaker engine: {hasPyannoteToken ? 'Pyannote when available, otherwise local heuristic' : 'Local heuristic speaker analysis'}.
                    Current mode: {speakerMode}.
                  </p>
                </div>

                <div className="space-y-2">
                  <Label>Number of clips</Label>
                  <div className="flex gap-2">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        type="button"
                        onClick={() => setSelectedClipCount(n)}
                        className={`flex h-10 w-10 items-center justify-center rounded-xl border text-sm font-medium transition-colors ${
                          selectedClipCount === n
                            ? 'border-sky-500 bg-sky-500 text-white'
                            : 'border-slate-200 bg-white text-slate-700 hover:border-sky-300 hover:bg-sky-50'
                        }`}
                      >
                        {n}
                      </button>
                    ))}
                  </div>
                  <p className="text-xs text-slate-500">How many Shorts clips to generate from this video.</p>
                </div>

                <div className="space-y-2">
                  <Label>Render profile</Label>
                  <div className="grid gap-2 sm:grid-cols-3">
                    {Object.entries(renderProfiles).map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setSelectedRenderProfile(key)}
                        className={`rounded-2xl border px-3 py-3 text-left text-sm transition-colors ${
                          selectedRenderProfile === key
                            ? 'border-sky-500 bg-sky-500 text-white'
                            : 'border-slate-200 bg-white text-slate-700 hover:border-sky-300 hover:bg-sky-50'
                        }`}
                      >
                        <span className="block font-medium">{label}</span>
                        <span className={`mt-1 block text-xs ${selectedRenderProfile === key ? 'text-sky-100' : 'text-slate-500'}`}>
                          {key === 'fast' ? 'Fast previews and iteration' : key === 'balanced' ? 'Daily default for local runs' : 'Highest finish quality'}
                        </span>
                      </button>
                    ))}
                  </div>
                  <p className="text-xs text-slate-500">Fast for iteration, balanced for normal use, studio for final delivery.</p>
                </div>

                <div className="space-y-3 rounded-[24px] border border-slate-200 bg-white p-4">
                  <div>
                    <p className="font-semibold text-slate-950">Subtitle Workbench</p>
                    <p className="mt-1 text-xs text-slate-500">Tune the calm, premium style before running a full render.</p>
                  </div>

                  <div className="space-y-2">
                    <Label>Font preset</Label>
                    <div className="flex flex-wrap gap-2">
                      {fontPresetOptions.map((preset) => (
                        <button
                          key={preset}
                          type="button"
                          onClick={() => setSelectedFontPreset(preset)}
                          className={`rounded-xl border px-3 py-2 text-sm transition-colors ${
                            selectedFontPreset === preset
                              ? 'border-sky-500 bg-sky-500 text-white'
                              : 'border-slate-200 bg-slate-50 text-slate-700 hover:border-sky-300 hover:bg-sky-50'
                          }`}
                        >
                          {preset}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label>Color preset</Label>
                    <div className="flex flex-wrap gap-2">
                      {colorPresetOptions.map((preset) => (
                        <button
                          key={preset}
                          type="button"
                          onClick={() => setSelectedColorPreset(preset)}
                          className={`rounded-xl border px-3 py-2 text-sm transition-colors ${
                            selectedColorPreset === preset
                              ? 'border-sky-500 bg-sky-500 text-white'
                              : 'border-slate-200 bg-slate-50 text-slate-700 hover:border-sky-300 hover:bg-sky-50'
                          }`}
                        >
                          {preset}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="previewTitle">Preview header</Label>
                    <Input
                      id="previewTitle"
                      value={previewTitle}
                      onChange={(event) => setPreviewTitle(event.target.value)}
                      autoComplete="off"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="previewReason">Preview subheader</Label>
                    <Input
                      id="previewReason"
                      value={previewReason}
                      onChange={(event) => setPreviewReason(event.target.value)}
                      autoComplete="off"
                    />
                  </div>

                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
                    <div className="mb-3 flex items-center justify-between">
                      <p className="text-sm font-medium text-slate-900">Live preview</p>
                      <span className="text-xs text-slate-500">{previewLoading ? 'Updating…' : 'Synced'}</span>
                    </div>

                    {previewData ? (
                      <div className="space-y-3">
                        {previewData.headerImages[0] ? (
                          <img src={previewData.headerImages[0]} alt="Subtitle header preview" className="w-full rounded-2xl border border-slate-200" />
                        ) : null}
                        <div className="grid gap-3 sm:grid-cols-2">
                          {previewData.subtitleFrames.slice(0, 2).map((cue) => (
                            <div key={cue.cue} className="space-y-2">
                              {cue.frames.dark ? (
                                <img src={cue.frames.dark} alt={`Subtitle preview ${cue.cue}`} className="w-full rounded-2xl border border-slate-200" />
                              ) : null}
                              <p className="text-xs text-slate-500">{cue.text}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <p className="text-sm text-slate-500">Preparing subtitle preview…</p>
                    )}
                  </div>
                </div>

                <Button className="h-12 w-full rounded-2xl bg-sky-600 text-white hover:bg-sky-700" type="submit" disabled={!canSubmit}>
                  {isSubmitting || isWorking ? (
                    <>
                      <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> Job running
                    </>
                  ) : (
                    <>
                      <PlaySquare className="mr-2 h-4 w-4" /> {startButtonLabel}
                    </>
                  )}
                </Button>

                {!hasAvailableApiKey ? (
                  <p className="text-sm text-amber-700">Add a Gemini key here or place `GEMINI_API_KEY` in `.env` before starting the render.</p>
                ) : null}

                {requestError ? <p className="text-sm text-rose-600">{requestError}</p> : null}
              </form>
            ) : (
              <div className="space-y-6">
                <div className="flex items-start justify-between gap-4 rounded-[26px] border border-sky-100 bg-sky-50/70 px-5 py-5">
                  <div className="space-y-2">
                    <p className="app-kicker text-sm font-medium text-sky-700">Current step</p>
                    <p className="text-xl font-semibold text-slate-950">{statusTitles[job.status]}</p>
                    <p className="app-copy text-sm leading-6 text-slate-600">{job.message ?? stageDescriptions[job.status]}</p>
                    {job.stageProgress != null ? (
                      <p className="text-xs text-slate-500">Step progress: {job.stageProgress}%</p>
                    ) : null}
                  </div>
                  <Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'} className="shrink-0 border-sky-200 bg-white text-slate-700">
                    {job.status}
                  </Badge>
                </div>

                <div className="space-y-3">
                  <div className="flex items-center justify-between text-sm text-slate-500">
                    <span>Progress</span>
                    <span>{progressValue}%</span>
                  </div>
                  <Progress value={progressValue} />
                </div>

                {etaLabel ? (
                  <div className="rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-950">
                    Estimated time left: {etaLabel}
                  </div>
                ) : null}

                {job.status === 'queued' && (job.queuePosition ?? 0) > 0 ? (
                  <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                    Queue position: {job.queuePosition}. The current render finishes before this job starts.
                  </div>
                ) : null}

                <div className="grid gap-3 sm:grid-cols-2">
                  {Object.entries(statusTitles)
                    .filter(([status]) => status !== 'idle' && status !== 'failed')
                    .map(([status, title]) => {
                      const active = progressByStatus[job.status] >= progressByStatus[status as JobStatus]
                      return (
                        <div
                          key={status}
                          className={`rounded-2xl border px-4 py-3 text-sm ${
                            active ? 'border-amber-200 bg-amber-50 text-slate-900' : 'border-slate-200 bg-slate-50 text-slate-500'
                          }`}
                        >
                          {title}
                        </div>
                      )
                    })}
                </div>

                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 text-sm leading-6 text-slate-700">
                  <p className="font-semibold text-slate-900">What happens now</p>
                  <p className="mt-2">{stageDescriptions[job.status]}</p>
                  <p className="mt-3 text-slate-500">Requested clips: {effectiveClipCount}. Output profile: {job.result?.renderProfile ?? currentRenderProfileLabel}. Finished files are saved locally in the outputs folder and will appear below when ready.</p>
                </div>

                {job.error ? <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{job.error}</p> : null}

                <div className="space-y-3">
                  <div>
                    <p className="app-heading text-lg text-slate-950">Live activity</p>
                    <p className="mt-1 text-sm text-slate-500">The latest exact messages from the local process.</p>
                  </div>

                {recentLogs.length > 0 ? (
                  <div className="space-y-3">
                    {recentLogs.map((entry, index) => (
                      <div key={`${entry.time}-${index}`} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                        <div className="flex items-center justify-between gap-3 text-xs uppercase tracking-[0.12em] text-slate-500">
                          <span>{entry.stage}</span>
                          <span>{formatLogTime(entry.time)}</span>
                        </div>
                        <p className="mt-2 text-sm text-slate-700">{entry.message}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-sm text-slate-500">
                    No backend activity yet.
                  </div>
                )}
                </div>

                <div className="space-y-4 rounded-[26px] border border-slate-200 bg-white p-5 shadow-[0_12px_30px_rgba(66,124,184,0.08)]">
                  <div>
                    <p className="app-heading text-lg text-slate-950">Downloads</p>
                    <p className="mt-1 text-sm text-slate-500">When the render finishes, your files appear here.</p>
                  </div>

                {job.status === 'completed' && job.result && jobId ? (
                  <>
                    <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
                      <div className="mb-2 flex items-center gap-2 font-medium">
                        <CheckCircle2 className="h-4 w-4" /> {job.result.clipCount} clip(s) ready
                      </div>
                      <p>{job.result.title ?? 'Your first clip is ready.'}</p>
                      <p className="mt-2 text-emerald-900">Quality profile: {job.result.renderProfile ?? currentRenderProfileLabel}</p>
                      <p className="mt-2 text-emerald-800/80">Saved locally in {job.result.outputDir}</p>
                    </div>

                    <div className="space-y-3">
                      {job.result.clips.map((clip) => {
                        const fb = clipFeedback[clip.index]
                        return (
                        <div key={clip.index} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                            <div className="space-y-2">
                              <p className="text-sm font-semibold text-slate-950">{clip.title ?? `Clip ${clip.index}`}</p>
                              <p className="text-sm text-slate-600">{clip.reason ?? 'Gemini selected a strong moment from the source video.'}</p>
                              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                                <span>{clip.start.toFixed(1)}s to {clip.end.toFixed(1)}s</span>
                                {clip.contentType ? (
                                  <Badge variant="outline" className="border-slate-300 text-xs">{clip.contentType}</Badge>
                                ) : null}
                                {clip.analytics?.confidence != null ? (
                                  <span className="text-slate-400">conf {String(clip.analytics.confidence)}</span>
                                ) : null}
                                {clip.analytics?.speakerTrackingMode != null ? (
                                  <span className="text-slate-400">focus {String(clip.analytics.speakerTrackingMode)}</span>
                                ) : null}
                                {clip.analytics?.speakerCountEstimate != null ? (
                                  <span className="text-slate-400">{String(clip.analytics.speakerCountEstimate)} speaker(s)</span>
                                ) : null}
                                {clip.analytics?.speakerSwitches != null ? (
                                  <span className="text-slate-400">{String(clip.analytics.speakerSwitches)} switches</span>
                                ) : null}
                                {clip.analytics?.speakerBalance != null ? (
                                  <span className="text-slate-400">balance {String(clip.analytics.speakerBalance)}</span>
                                ) : null}
                                {clip.analytics?.speakerTrackingStability != null ? (
                                  <span className="text-slate-400">tracking {String(clip.analytics.speakerTrackingStability)}</span>
                                ) : null}
                                {clip.analytics?.audioSpeakerCount != null ? (
                                  <span className="text-slate-400">audio {String(clip.analytics.audioSpeakerCount)} speaker(s)</span>
                                ) : null}
                                {clip.analytics?.audioSpeakerSwitches != null ? (
                                  <span className="text-slate-400">audio switches {String(clip.analytics.audioSpeakerSwitches)}</span>
                                ) : null}
                                {clip.analytics?.audioSpeakerConfidence != null ? (
                                  <span className="text-slate-400">audio conf {String(clip.analytics.audioSpeakerConfidence)}</span>
                                ) : null}
                                {clip.analytics?.audioSpeakerProvider != null ? (
                                  <span className="text-slate-400">audio engine {String(clip.analytics.audioSpeakerProvider)}</span>
                                ) : null}
                              </div>
                            </div>
                            <Button asChild className="bg-sky-600 text-white hover:bg-sky-700 sm:w-auto">
                              <a href={`/api/jobs/${jobId}/download/video/${clip.index}`}>
                                <Download className="mr-2 h-4 w-4" /> Download clip
                              </a>
                            </Button>
                          </div>

                          {/* Feedback section */}
                          <div className="mt-3 border-t border-slate-200 pt-3">
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-slate-500">Rate this clip:</span>
                              <button
                                type="button"
                                onClick={() => void handleRating(clip.index, 'good')}
                                className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                                  fb?.rating === 'good'
                                    ? 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-300'
                                    : 'bg-slate-100 text-slate-500 hover:bg-emerald-50 hover:text-emerald-600'
                                }`}
                              >
                                <ThumbsUp className="h-3.5 w-3.5" /> Good
                              </button>
                              <button
                                type="button"
                                onClick={() => void handleRating(clip.index, 'bad')}
                                className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                                  fb?.rating === 'bad'
                                    ? 'bg-red-100 text-red-700 ring-1 ring-red-300'
                                    : 'bg-slate-100 text-slate-500 hover:bg-red-50 hover:text-red-600'
                                }`}
                              >
                                <ThumbsDown className="h-3.5 w-3.5" /> Bad
                              </button>
                              {fb?.saved ? (
                                <span className="text-xs text-emerald-600">Saved</span>
                              ) : null}
                            </div>

                            {fb?.rating ? (
                              <div className="mt-2 flex flex-wrap gap-1.5">
                                {feedbackTags
                                  .filter(t => fb.rating === 'good' ? t.positive : !t.positive)
                                  .map(tag => (
                                    <button
                                      key={tag.id}
                                      type="button"
                                      onClick={() => void handleTagToggle(clip.index, tag.id)}
                                      className={`rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors ${
                                        fb.tags.includes(tag.id)
                                          ? fb.rating === 'good'
                                            ? 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200'
                                            : 'bg-red-100 text-red-700 ring-1 ring-red-200'
                                          : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                                      }`}
                                    >
                                      {tag.label}
                                    </button>
                                  ))}
                              </div>
                            ) : null}
                          </div>
                        </div>
                        )
                      })}
                    </div>

                    <Button asChild variant="secondary" className="w-full">
                      <a href={`/api/jobs/${jobId}/download/transcript`}>
                        <Download className="mr-2 h-4 w-4" /> Download transcript
                      </a>
                    </Button>

                    {/* Analytics section */}
                    <div className="border-t border-slate-200 pt-4">
                      <button
                        type="button"
                        onClick={() => {
                          setShowAnalytics(prev => !prev)
                          if (!analyticsData) void loadAnalytics()
                        }}
                        className="flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors"
                      >
                        <BarChart3 className="h-4 w-4" />
                        {showAnalytics ? 'Hide' : 'Show'} performance insights
                      </button>

                      {showAnalytics && analyticsData ? (
                        <div className="mt-3 space-y-3 rounded-xl border border-slate-200 bg-white p-4 text-sm">
                          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <div className="rounded-lg bg-slate-50 p-3 text-center">
                              <p className="text-lg font-bold text-slate-900">{analyticsData.totalClips}</p>
                              <p className="text-xs text-slate-500">Total clips</p>
                            </div>
                            <div className="rounded-lg bg-slate-50 p-3 text-center">
                              <p className="text-lg font-bold text-slate-900">{analyticsData.totalRated}</p>
                              <p className="text-xs text-slate-500">Rated</p>
                            </div>
                            <div className="rounded-lg bg-emerald-50 p-3 text-center">
                              <p className="text-lg font-bold text-emerald-700">{analyticsData.totalGood}</p>
                              <p className="text-xs text-emerald-600">Good</p>
                            </div>
                            <div className="rounded-lg bg-red-50 p-3 text-center">
                              <p className="text-lg font-bold text-red-700">{analyticsData.totalBad}</p>
                              <p className="text-xs text-red-600">Bad</p>
                            </div>
                          </div>

                          {analyticsData.overallApprovalRate != null ? (
                            <div className="rounded-lg bg-sky-50 p-3">
                              <p className="text-sm text-sky-800">
                                Overall approval rate: <span className="font-bold">{(analyticsData.overallApprovalRate * 100).toFixed(0)}%</span>
                                <span className="ml-1 text-xs text-sky-600">across {analyticsData.totalRated} rated clips</span>
                              </p>
                            </div>
                          ) : null}

                          {Object.keys(analyticsData.perContentType).length > 0 ? (
                            <div>
                              <p className="mb-2 text-xs font-medium text-slate-700">Per content type</p>
                              <div className="space-y-1.5">
                                {Object.entries(analyticsData.perContentType).map(([ct, stats]) => (
                                  <div key={ct} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs">
                                    <span className="font-medium text-slate-700">{ct}</span>
                                    <div className="flex items-center gap-3 text-slate-500">
                                      <span>{stats.clipCount} clips</span>
                                      {stats.avgConfidence != null ? <span>conf {stats.avgConfidence}</span> : null}
                                      {stats.approvalRate != null ? (
                                        <span className={stats.approvalRate >= 0.7 ? 'text-emerald-600' : stats.approvalRate < 0.5 ? 'text-red-600' : 'text-amber-600'}>
                                          {(stats.approvalRate * 100).toFixed(0)}% approved
                                        </span>
                                      ) : null}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ) : null}

                          <button
                            type="button"
                            onClick={() => void loadAnalytics()}
                            className="text-xs text-sky-600 hover:text-sky-800 transition-colors"
                          >
                            Refresh insights
                          </button>
                        </div>
                      ) : showAnalytics ? (
                        <p className="mt-2 text-xs text-slate-500">Loading insights...</p>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <div className="space-y-3 rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-5 text-sm text-slate-600">
                    <p className="font-medium text-slate-900">What you get after each run</p>
                    <p>- Up to five finished vertical clips ready to download</p>
                    <p>- High-quality 1080x1920 MP4 exports optimized for sharing</p>
                    <p>- A full transcript file for reference</p>
                    <p>- A saved local folder inside outputs for the exact job</p>
                  </div>
                )}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  )
}

export default App
