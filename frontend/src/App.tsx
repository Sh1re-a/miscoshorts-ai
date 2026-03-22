import { useEffect, useEffectEvent, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, Download, LoaderCircle, PlaySquare, RotateCcw } from 'lucide-react'

import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui/card'
import { Input } from './components/ui/input'
import { Label } from './components/ui/label'
import { Progress } from './components/ui/progress'

type JobStatus =
  | 'idle'
  | 'queued'
  | 'validating'
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
  renderProfile?: string
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

const defaultClipCount = 3

const progressByStatus: Record<JobStatus, number> = {
  idle: 0,
  queued: 8,
  validating: 14,
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
  validating: 'Validating subtitle compatibility',
  downloading: 'Downloading source video',
  transcribing: 'Transcribing audio',
  analyzing: 'Gemini is selecting clips',
  rendering: 'Rendering video',
  completed: 'Render complete',
  failed: 'Render failed',
}

const stageDescriptions: Record<JobStatus, string> = {
  idle: 'Paste a YouTube link, add your Gemini key, and start the default Shorts render flow.',
  queued: 'Your request reached the local app and is waiting to begin.',
  validating: 'Checking subtitle rendering compatibility and local requirements before the heavy work starts.',
  downloading: 'Downloading the source video and preparing local files.',
  transcribing: 'Whisper is transcribing the video. This is usually the longest step.',
  analyzing: 'Gemini is selecting the strongest clip moments from the transcript.',
  rendering: 'Rendering the final vertical video with dynamic subtitles and overlays.',
  completed: 'Everything is finished. Your files are ready below as high-quality 1080x1920 exports.',
  failed: 'The job stopped before finishing. Read the message below for the exact reason.',
}

const renderProfileLabel = 'Studio HQ 1080x1920 MP4'

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
  const startButtonLabel = isSubmitting || isWorking ? 'Job running' : 'Start studio render'
  const etaWindow = useMemo(() => getEtaWindow(job, defaultClipCount, nowMs), [job, nowMs])
  const etaLabel = etaWindow ? `${formatEta(etaWindow[0])} to ${formatEta(etaWindow[1])}` : null
  const hasStarted = job.status !== 'idle' || isSubmitting || jobId !== null
  const effectiveClipCount = job.result?.clipCount ?? job.clipCount ?? defaultClipCount

  function resetFlow() {
    setJobId(null)
    setJob({ status: 'idle' })
    setRequestError(null)
    setIsSubmitting(false)
    setOutputFilename('short_con_subs.mp4')
  }

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
          clipCount: defaultClipCount,
        }),
      })

      const payload = (await response.json()) as { jobId?: string; error?: string; status?: JobStatus; clipCount?: number }

      if (!response.ok || !payload.jobId) {
        throw new Error(payload.error ?? 'Could not start the job.')
      }

      setJobId(payload.jobId)
      setJob({
        status: payload.status ?? 'queued',
        message: `The job is running locally and will render ${payload.clipCount ?? defaultClipCount} clip(s).`,
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
                  <p className="font-semibold text-slate-900">{renderProfileLabel}</p>
                  <p className="mt-2">The app runs a focused Shorts workflow: 3 selected clips, centered reframing, stronger H.264 settings, AAC audio, and more dynamic voice-following subtitles.</p>
                  <p className="mt-2">Keep the launcher window open while the job runs. Finished files appear here and in the local outputs folder.</p>
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
                  </div>
                  <Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'} className="shrink-0 border-sky-200 bg-white text-slate-700">
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

                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 text-sm leading-6 text-slate-700">
                  <p className="font-semibold text-slate-900">What happens now</p>
                  <p className="mt-2">{stageDescriptions[job.status]}</p>
                  <p className="mt-3 text-slate-500">Requested clips: {effectiveClipCount}. Output profile: {job.result?.renderProfile ?? renderProfileLabel}. Finished files are saved locally in the outputs folder and will appear below when ready.</p>
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
                      <p className="mt-2 text-emerald-900">Quality profile: {job.result.renderProfile ?? renderProfileLabel}</p>
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
                            <Button asChild className="bg-sky-600 text-white hover:bg-sky-700 sm:w-auto">
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
