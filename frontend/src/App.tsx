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
  { value: 3, label: '3 clips', note: 'Balanced' },
  { value: 5, label: '5 clips', note: 'More choices' },
]

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

function App() {
  const [videoUrl, setVideoUrl] = useState('')
  const [apiKey, setApiKey] = useState(loadSavedApiKey)
  const [outputFilename, setOutputFilename] = useState('short_con_subs.mp4')
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobPayload>({ status: 'idle' })
  const [requestError, setRequestError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [apiKeyNotice, setApiKeyNotice] = useState(apiKey ? 'Saved locally in this browser.' : 'Not saved yet.')
  const [clipCount, setClipCount] = useState<ClipCountOption>(1)

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
    <main className="min-h-screen bg-gradient-to-b from-sky-50 via-white to-slate-50 text-slate-900">
      <div className="mx-auto flex min-h-screen max-w-5xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8 lg:py-10">
        <header className="space-y-4 rounded-[28px] border border-sky-100 bg-white px-6 py-6 shadow-[0_20px_60px_rgba(148,184,255,0.12)]">
          <Badge variant="outline" className="app-kicker border-sky-200 bg-sky-50 text-sky-700">
            Local Render Dashboard
          </Badge>
          <div className="space-y-2">
            <h1 className="app-display text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl md:text-5xl">
              Simple status-first video render
            </h1>
            <p className="app-copy max-w-2xl text-sm text-slate-600 sm:text-base">
              Paste a YouTube link, start the job, and follow each stage clearly while the backend downloads, transcribes, analyzes, and renders.
            </p>
          </div>
        </header>

        <section className="grid gap-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Card className="border-sky-100 bg-white shadow-[0_18px_50px_rgba(148,184,255,0.10)]">
            <CardHeader>
              <CardTitle className="app-heading text-2xl text-slate-950">Start job</CardTitle>
              <CardDescription className="text-slate-600">
                  Keep it simple. The app now uses a cleaner premium subtitle profile automatically.
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
                    placeholder="Paste your Gemini key"
                    autoComplete="off"
                    required
                  />
                  <p className="text-xs text-slate-500">{apiKeyNotice}</p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="outputFilename">Output file</Label>
                  <Input
                    id="outputFilename"
                    value={outputFilename}
                    onChange={(event) => setOutputFilename(event.target.value)}
                    placeholder="short_con_subs.mp4"
                  />
                </div>

                <div className="space-y-3">
                  <Label>How many clips</Label>
                  <div className="grid gap-3 sm:grid-cols-3">
                    {clipCountOptions.map((option) => {
                      const selected = clipCount === option.value
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => setClipCount(option.value)}
                          className={`rounded-2xl border px-4 py-4 text-left transition ${
                            selected
                              ? 'border-sky-300 bg-sky-50 shadow-[0_10px_24px_rgba(125,170,255,0.14)]'
                              : 'border-slate-200 bg-white hover:border-sky-200 hover:bg-sky-50/60'
                          }`}
                        >
                          <p className="text-base font-semibold text-slate-900">{option.label}</p>
                          <p className="mt-1 text-sm text-slate-500">{option.note}</p>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="rounded-2xl border border-sky-100 bg-sky-50 px-4 py-4 text-sm text-slate-700">
                  Default subtitle settings now use a cleaner premium look with softer highlight and sharper typography.
                </div>

                <Button className="w-full bg-sky-600 text-white hover:bg-sky-700" type="submit" disabled={isSubmitting || isWorking}>
                  {isSubmitting || isWorking ? (
                    <>
                      <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> Job running
                    </>
                  ) : (
                    <>
                      <PlaySquare className="mr-2 h-4 w-4" /> Start render
                    </>
                  )}
                </Button>

                {requestError ? <p className="text-sm text-rose-600">{requestError}</p> : null}
              </form>
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card className="border-sky-100 bg-white shadow-[0_18px_50px_rgba(148,184,255,0.10)]">
              <CardHeader>
                <CardTitle className="app-heading text-2xl text-slate-950">Status</CardTitle>
                <CardDescription className="text-slate-600">
                  Clear live progress from your local backend.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="flex items-start justify-between gap-4 rounded-2xl border border-sky-100 bg-sky-50 px-4 py-4">
                  <div className="space-y-2">
                    <p className="app-kicker text-sm font-medium text-sky-700">Current step</p>
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

                <div className="grid gap-3 sm:grid-cols-2">
                  {Object.entries(statusTitles)
                    .filter(([status]) => status !== 'idle' && status !== 'failed')
                    .map(([status, title]) => {
                      const active = progressByStatus[job.status] >= progressByStatus[status as JobStatus]
                      return (
                        <div
                          key={status}
                          className={`rounded-2xl border px-4 py-3 text-sm ${
                            active ? 'border-sky-200 bg-sky-50 text-slate-900' : 'border-slate-200 bg-slate-50 text-slate-500'
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

            <Card className="border-sky-100 bg-white shadow-[0_18px_50px_rgba(148,184,255,0.10)]">
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

            <Card className="border-sky-100 bg-white shadow-[0_18px_50px_rgba(148,184,255,0.10)]">
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
                  <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-5 text-sm text-slate-500">
                    Start a render to unlock downloads here.
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
