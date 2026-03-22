import { useEffect, useEffectEvent, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, Download, LoaderCircle, PlaySquare } from 'lucide-react'

import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui/card'
import { Input } from './components/ui/input'
import { Label } from './components/ui/label'
import { Progress } from './components/ui/progress'

type JobStatus =
  | 'idle'
  | 'queued'
  | 'downloading'
  | 'transcribing'
  | 'analyzing'
  | 'rendering'
  | 'completed'
  | 'failed'

type JobLog = {
  time: number
  stage: string
  message: string
}

type JobResult = {
  title?: string
  reason?: string
  start: number
  end: number
  outputFilename: string
  outputDir: string
  clipCount: number
  clips: JobClip[]
}

type JobClip = {
  index: number
  title?: string
  reason?: string
  start: number
  end: number
  outputFilename: string
}

type JobPayload = {
  status: JobStatus
  message?: string
  error?: string
  logs?: JobLog[]
  result?: JobResult
  clipCount?: number
  createdAt?: number
  updatedAt?: number
}

type BootstrapPayload = {
  hasConfiguredApiKey: boolean
  frontendBuilt: boolean
}

type ClipCountOption = 1 | 3 | 5

const progressByStatus: Record<JobStatus, number> = {
  idle: 0,
  queued: 8,
  downloading: 20,
  transcribing: 48,
  analyzing: 68,
  rendering: 88,
  completed: 100,
  failed: 100,
}

const statusTitles: Record<JobStatus, string> = {
  idle: 'Ready to start',
  queued: 'Queued in local backend',
  downloading: 'Downloading source video',
  transcribing: 'Transcribing audio',
  analyzing: 'Gemini is selecting clips',
  rendering: 'Rendering video',
  completed: 'Render complete',
  failed: 'Render failed',
}

const clipCountOptions: Array<{ value: ClipCountOption; label: string; note: string }> = [
  { value: 1, label: '1 clip', note: 'Fastest' },
  { value: 3, label: '3 clips', note: 'More coverage' },
]

const subtitlePreview = {
  label: 'Fixed subtitle direction',
  title: 'Iran\'s "Taboo" Leader: The Secret Behind His Rise',
  reason: 'A cleaner editorial top panel with stronger contrast and calmer hierarchy.',
  caption: 'a really searing statement',
}

const apiKeyStorageKey = 'miscoshorts.apiKey'

function loadSavedApiKey() {
  try {
    return window.localStorage.getItem(apiKeyStorageKey) ?? ''
  } catch {
    return ''
  }
}

function formatLogTime(timestamp: number) {
  return new Intl.DateTimeFormat('sv-SE', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(timestamp * 1000))
}

function formatEta(seconds: number) {
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

function getEtaWindow(job: JobPayload, selectedClipCount: number, nowMs: number) {
  const effectiveClipCount = job.result?.clipCount ?? job.clipCount ?? selectedClipCount
  const stageStartedAt = (job.updatedAt ?? job.createdAt ?? nowMs / 1000) * 1000
  const elapsedSeconds = Math.max(0, Math.round((nowMs - stageStartedAt) / 1000))

  switch (job.status) {
    case 'queued':
      return [Math.max(0, 20 - elapsedSeconds), Math.max(0, 60 - elapsedSeconds)]
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

function App() {
  const [videoUrl, setVideoUrl] = useState('')
  const [apiKey, setApiKey] = useState(loadSavedApiKey)
  const [hasConfiguredApiKey, setHasConfiguredApiKey] = useState(false)
  const [outputFilename, setOutputFilename] = useState('short_con_subs.mp4')
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobPayload>({ status: 'idle' })
  const [requestError, setRequestError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [apiKeyNotice, setApiKeyNotice] = useState(apiKey ? 'Saved locally in this browser.' : 'Not saved yet.')
  const [clipCount, setClipCount] = useState<ClipCountOption>(3)
  const [nowMs, setNowMs] = useState(() => Date.now())

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
  const startButtonLabel = isSubmitting || isWorking ? 'Job running' : `Render ${clipCount} clip${clipCount > 1 ? 's' : ''}`
  const etaWindow = useMemo(() => getEtaWindow(job, clipCount, nowMs), [job, clipCount, nowMs])
  const etaLabel = etaWindow ? `${formatEta(etaWindow[0])} to ${formatEta(etaWindow[1])}` : null

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
          clipCount,
        }),
      })

      const payload = (await response.json()) as { jobId?: string; error?: string; status?: JobStatus; clipCount?: number }

      if (!response.ok || !payload.jobId) {
        throw new Error(payload.error ?? 'Could not start the job.')
      }

      setJobId(payload.jobId)
      setJob({
        status: payload.status ?? 'queued',
        message: `The job is running locally and will render ${payload.clipCount ?? clipCount} clip(s).`,
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
    <main className="bg-ink min-h-screen text-slate-900">
      <div className="mx-auto flex min-h-screen max-w-5xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8 lg:py-10">
        <header className="bg-porcelain relative overflow-hidden rounded-[32px] border border-stone-200/80 px-6 py-7 shadow-[0_28px_80px_rgba(62,43,24,0.08)]">
          <div className="hero-orb hero-orb-left" />
          <div className="hero-orb hero-orb-right" />
          <div className="grid-fade" />
          <div className="relative grid gap-5 lg:grid-cols-[minmax(0,1fr)_19rem] lg:items-end">
            <div className="space-y-4">
              <Badge variant="outline" className="app-kicker border-sand/60 bg-sand/10 text-sand">
                Local Render Dashboard
              </Badge>
              <div className="space-y-2">
                <h1 className="app-display text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl md:text-5xl">
                  Cleaner shorts, clearer typography
                </h1>
                <p className="app-copy max-w-2xl text-sm text-slate-600 sm:text-base">
                  The app now focuses on one stronger subtitle direction: a high-contrast editorial header, calmer body copy, and a cleaner preview before you render.
                </p>
              </div>
            </div>

            <div className="rounded-[28px] border border-white/70 bg-white/70 p-5 backdrop-blur-xl">
              <p className="app-kicker text-xs text-slate-500">Current direction</p>
              <p className="mt-2 app-heading text-xl text-slate-950">Studio editorial</p>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                Fewer choices in the UI, stronger readability in the render, and a preview that matches the exported look.
              </p>
              <div className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-slate-600">
                Default demo setup: 3 clips, fixed subtitle style, clearer top overlay.
              </div>
            </div>
          </div>
        </header>

        <section className="grid gap-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Card className="border-stone-200/80 bg-white/95 shadow-[0_20px_55px_rgba(62,43,24,0.08)] backdrop-blur-sm">
            <CardHeader>
              <CardTitle className="app-heading text-2xl text-slate-950">Start job</CardTitle>
              <CardDescription className="text-slate-600">
                Only the important inputs remain. Subtitle styling is fixed to one cleaner premium profile.
              </CardDescription>
            </CardHeader>
            <CardContent>
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

                <div className="space-y-2">
                  <Label htmlFor="outputFilename">Output file</Label>
                  <Input
                    id="outputFilename"
                    value={outputFilename}
                    onChange={(event) => setOutputFilename(event.target.value)}
                    placeholder="short_con_subs.mp4"
                  />
                  <p className="text-xs text-slate-500">Optional. Leave it as-is if you just want the clean default export.</p>
                </div>

                <div className="rounded-[28px] border border-stone-200 bg-stone-50/70 p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                      <p className="app-kicker text-xs text-slate-500">Subtitle direction</p>
                      <p className="app-heading text-lg text-slate-950">Studio editorial</p>
                      <p className="text-sm leading-6 text-slate-600">
                        Stronger top headline panel, cleaner caption contrast, and fewer distracting style decisions.
                      </p>
                    </div>
                    <Badge variant="outline" className="border-stone-300 bg-white text-slate-700">
                      Fixed
                    </Badge>
                  </div>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-[22px] border border-stone-200 bg-white px-4 py-4">
                    <p className="app-kicker text-[0.65rem] text-slate-500">Before you render</p>
                    <p className="mt-2 text-sm font-semibold text-slate-900">Keep the launcher window open</p>
                    <p className="mt-2 text-sm leading-6 text-slate-600">
                      The browser is only the interface. The launcher window keeps the local server and render process alive.
                    </p>
                  </div>
                  <div className="rounded-[22px] border border-stone-200 bg-white px-4 py-4">
                    <p className="app-kicker text-[0.65rem] text-slate-500">After render</p>
                    <p className="mt-2 text-sm font-semibold text-slate-900">Download here, files also save locally</p>
                    <p className="mt-2 text-sm leading-6 text-slate-600">
                      Every finished render appears in the downloads panel and is also stored in a new folder inside outputs.
                    </p>
                  </div>
                </div>

                <div className="rounded-[24px] border border-sky-200 bg-sky-50 px-4 py-4 text-sm leading-6 text-sky-950">
                  First launch can take longer while Windows prepares Python and FFmpeg. Later launches should usually reuse the same setup and open much faster.
                </div>

                <div className="space-y-3">
                  <Label>How many clips</Label>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {clipCountOptions.map((option) => {
                      const selected = clipCount === option.value
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => setClipCount(option.value)}
                          className={`rounded-[22px] border px-4 py-4 text-left transition ${
                            selected
                              ? 'border-amber-300 bg-white shadow-[0_12px_28px_rgba(176,123,42,0.12)]'
                              : 'border-stone-200 bg-white hover:border-stone-300 hover:bg-stone-50'
                          }`}
                        >
                          <p className="text-base font-semibold text-slate-900">{option.label}</p>
                          <p className="mt-1 text-sm text-slate-500">{option.note}</p>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="rounded-[24px] border border-stone-200 bg-stone-50 px-4 py-4 text-sm leading-6 text-slate-700">
                  The render now uses one refined subtitle setup by default. You preview the exact direction on the right instead of choosing between multiple weaker presets.
                </div>

                <Button className="w-full bg-amber-600 text-white hover:bg-amber-700" type="submit" disabled={!canSubmit}>
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
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card className="border-stone-200/80 bg-white/95 shadow-[0_20px_55px_rgba(62,43,24,0.08)] backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="app-heading text-2xl text-slate-950">Style preview</CardTitle>
                <CardDescription className="text-slate-600">
                  This is the direction used in the rendered clip. One stronger look, fewer style choices.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="preview-stage relative overflow-hidden rounded-[30px] border border-stone-200/80 p-3">
                  <div className="preview-video-frame aspect-[9/16]">
                    <div className="preview-safe-guide preview-safe-guide-top" />
                    <div className="preview-safe-guide preview-safe-guide-bottom" />
                    <div className="preview-safe-label preview-safe-label-top">headline safe zone</div>
                    <div className="preview-safe-label preview-safe-label-bottom">subtitle safe zone</div>
                    <div className="preview-portrait-glow" />
                    <div className="preview-portrait-frame">
                      <div className="preview-portrait-head" />
                      <div className="preview-portrait-face" />
                      <div className="preview-portrait-neck" />
                      <div className="preview-portrait-shirt" />
                      <div className="preview-glasses preview-glasses-left" />
                      <div className="preview-glasses preview-glasses-right" />
                      <div className="preview-glasses-bridge" />
                    </div>
                    <div className="preview-video-overlay" />

                    <div className="preview-top-panel">
                      <div className="preview-top-accent" />
                      <div className="preview-header-stack">
                        <p className="preview-panel-label">{subtitlePreview.label}</p>
                        <p className="preview-title">{subtitlePreview.title}</p>
                        <p className="preview-reason">{subtitlePreview.reason}</p>
                      </div>
                    </div>

                    <div className="preview-subtitle-shadow" />
                    <div className="preview-subtitle-wrap">
                      <div className="preview-subtitle-box">
                        <p className="preview-subtitle">{subtitlePreview.caption}</p>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="rounded-[22px] border border-stone-200 bg-stone-50 px-4 py-3">
                    <p className="app-kicker text-[0.65rem] text-slate-500">Headline</p>
                    <p className="mt-2 text-sm font-semibold text-slate-900">Dark panel with clear hierarchy</p>
                  </div>
                  <div className="rounded-[22px] border border-stone-200 bg-stone-50 px-4 py-3">
                    <p className="app-kicker text-[0.65rem] text-slate-500">Reason</p>
                    <p className="mt-2 text-sm font-semibold text-slate-900">Softer secondary copy, still readable</p>
                  </div>
                  <div className="rounded-[22px] border border-stone-200 bg-stone-50 px-4 py-3">
                    <p className="app-kicker text-[0.65rem] text-slate-500">Captions</p>
                    <p className="mt-2 text-sm font-semibold text-slate-900">Cleaner bottom focus with less visual noise</p>
                  </div>
                </div>

                <div className="rounded-[24px] border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm leading-6 text-emerald-950">
                  Customer-ready: the preview now shows where title and subtitle sit on a portrait frame, so the chosen style is easier to trust before rendering.
                </div>
              </CardContent>
            </Card>

            <Card className="border-stone-200/80 bg-white/95 shadow-[0_20px_55px_rgba(62,43,24,0.08)] backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="app-heading text-2xl text-slate-950">Status</CardTitle>
                <CardDescription className="text-slate-600">
                  Clear live progress from your local backend.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="flex items-start justify-between gap-4 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-4">
                  <div className="space-y-2">
                    <p className="app-kicker text-sm font-medium text-amber-700">Current step</p>
                    <p className="text-xl font-semibold text-slate-950">{statusTitles[job.status]}</p>
                    <p className="app-copy text-sm text-slate-600">{job.message ?? 'No active job yet.'}</p>
                  </div>
                  <Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'} className="shrink-0">
                    {job.status}
                  </Badge>
                </div>

                <div className="space-y-3">
                  <div className="flex items-center justify-between text-sm text-slate-500">
                    <span>Progress</span>
                    <span>{progressByStatus[job.status]}%</span>
                  </div>
                  <Progress value={progressByStatus[job.status]} />
                </div>

                {etaLabel ? (
                  <div className="rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-950">
                    Estimated time left: {etaLabel}
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

                {job.error ? <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{job.error}</p> : null}
              </CardContent>
            </Card>

            <Card className="border-stone-200/80 bg-white/95 shadow-[0_20px_55px_rgba(62,43,24,0.08)] backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="app-heading text-xl text-slate-950">Activity log</CardTitle>
                <CardDescription className="text-slate-600">
                  Latest backend messages. This should match what the terminal shows.
                </CardDescription>
              </CardHeader>
              <CardContent>
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
              </CardContent>
            </Card>

            <Card className="border-stone-200/80 bg-white/95 shadow-[0_20px_55px_rgba(62,43,24,0.08)] backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="app-heading text-xl text-slate-950">Customer flow</CardTitle>
                <CardDescription className="text-slate-600">
                  The app is now structured around one clean path from input to finished files.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-3 sm:grid-cols-2">
                  {[
                    'Paste the YouTube link.',
                    'Use the saved Gemini key or add one now.',
                    'Render 1 or 3 clips with the fixed subtitle style.',
                    'Download the clips and transcript when the job completes.',
                  ].map((step, index) => (
                    <div key={step} className="rounded-[22px] border border-stone-200 bg-stone-50 px-4 py-4">
                      <p className="app-kicker text-[0.65rem] text-slate-500">Step {index + 1}</p>
                      <p className="mt-2 text-sm font-semibold leading-6 text-slate-900">{step}</p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card className="border-stone-200/80 bg-white/95 shadow-[0_20px_55px_rgba(62,43,24,0.08)] backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="app-heading text-xl text-slate-950">Downloads</CardTitle>
                <CardDescription className="text-slate-600">
                  When the render finishes, the files appear here.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {job.status === 'completed' && job.result && jobId ? (
                  <>
                    <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
                      <div className="mb-2 flex items-center gap-2 font-medium">
                        <CheckCircle2 className="h-4 w-4" /> {job.result.clipCount} clip(s) ready
                      </div>
                      <p>{job.result.title ?? 'Your first clip is ready.'}</p>
                      <p className="mt-2 text-emerald-800/80">Saved locally in {job.result.outputDir}</p>
                    </div>

                    <div className="space-y-3">
                      {job.result.clips.map((clip) => (
                        <div key={clip.index} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                            <div className="space-y-2">
                              <p className="text-sm font-semibold text-slate-950">{clip.title ?? `Clip ${clip.index}`}</p>
                              <p className="text-sm text-slate-600">{clip.reason ?? 'Gemini selected a strong moment from the source video.'}</p>
                              <p className="text-xs text-slate-500">{clip.start.toFixed(1)}s to {clip.end.toFixed(1)}s</p>
                            </div>
                            <Button asChild className="bg-amber-600 text-white hover:bg-amber-700 sm:w-auto">
                              <a href={`/api/jobs/${jobId}/download/video/${clip.index}`}>
                                <Download className="mr-2 h-4 w-4" /> Download clip
                              </a>
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>

                    <Button asChild variant="secondary" className="w-full">
                      <a href={`/api/jobs/${jobId}/download/transcript`}>
                        <Download className="mr-2 h-4 w-4" /> Download transcript
                      </a>
                    </Button>
                  </>
                ) : (
                  <div className="space-y-3 rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-5 text-sm text-slate-600">
                    <p className="font-medium text-slate-900">What you get after each run</p>
                    <p>- One or three finished vertical clips ready to download</p>
                    <p>- A full transcript file for reference</p>
                    <p>- A saved local folder inside outputs for the exact job</p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </section>
      </div>
    </main>
  )
}

export default App
