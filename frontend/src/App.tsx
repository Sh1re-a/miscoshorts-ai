import { useEffect, useEffectEvent, useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, Download, LoaderCircle, Sparkles, Video } from 'lucide-react'

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

type JobResult = {
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
  result?: JobResult
}

const progressByStatus: Record<JobStatus, number> = {
  idle: 0,
  queued: 8,
  downloading: 22,
  transcribing: 48,
  analyzing: 70,
  rendering: 90,
  completed: 100,
  failed: 100,
}

const labels: Array<{ status: JobStatus; label: string }> = [
  { status: 'queued', label: 'Queued' },
  { status: 'downloading', label: 'Download' },
  { status: 'transcribing', label: 'Whisper' },
  { status: 'analyzing', label: 'Gemini' },
  { status: 'rendering', label: 'Render' },
  { status: 'completed', label: 'Done' },
]

function App() {
  const [videoUrl, setVideoUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [outputFilename, setOutputFilename] = useState('short_con_subs.mp4')
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobPayload>({ status: 'idle' })
  const [requestError, setRequestError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

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
    }, 2000)

    return () => window.clearInterval(intervalId)
  }, [jobId, pollJob])

  const isWorking = !['idle', 'completed', 'failed'].includes(job.status)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setRequestError(null)
    setJob({ status: 'queued', message: 'Preparing local job...' })
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
        }),
      })

      const payload = (await response.json()) as { jobId?: string; error?: string; status?: JobStatus }

      if (!response.ok || !payload.jobId) {
        throw new Error(payload.error ?? 'Could not start the job.')
      }

      setJobId(payload.jobId)
      setJob({ status: payload.status ?? 'queued', message: 'Job started in the local backend.' })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unexpected error'
      setJob({ status: 'failed', error: message })
      setRequestError(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <main className="min-h-screen bg-ink text-white">
      <div className="absolute inset-0 -z-10 overflow-hidden">
        <div className="hero-orb hero-orb-left" />
        <div className="hero-orb hero-orb-right" />
        <div className="grid-fade" />
      </div>

      <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-10 px-6 py-10 lg:px-10">
        <header className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl space-y-4">
            <Badge variant="outline" className="border-white/20 bg-white/5 text-sand">
              Browser-first Short Generator
            </Badge>
            <h1 className="max-w-2xl text-4xl font-semibold tracking-tight text-white sm:text-5xl lg:text-6xl">
              Turn one YouTube link into a captioned short from your browser.
            </h1>
            <p className="max-w-2xl text-base text-white/70 sm:text-lg">
              This local dashboard runs on top of your Python pipeline. Paste a video URL, add a Gemini key,
              and let the app download, transcribe, analyze, crop, subtitle, and export the final clip.
            </p>
          </div>

          <Card className="w-full max-w-sm border-white/10 bg-white/5 backdrop-blur-xl">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg text-white">
                <Sparkles className="h-5 w-5 text-sand" /> Local mode
              </CardTitle>
              <CardDescription className="text-white/60">
                Your browser talks to a local Python backend. Nothing here is designed as a hosted SaaS app.
              </CardDescription>
            </CardHeader>
          </Card>
        </header>

        <section className="grid gap-6 lg:grid-cols-[1.15fr_0.85fr]">
          <Card className="border-white/10 bg-night/70 shadow-2xl shadow-black/20 backdrop-blur-xl">
            <CardHeader>
              <CardTitle className="text-2xl text-white">Create a Short</CardTitle>
              <CardDescription className="text-white/60">
                Minimal input, local processing, one output clip.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-6" onSubmit={handleSubmit}>
                <div className="space-y-2">
                  <Label htmlFor="videoUrl">YouTube video URL</Label>
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
                  <Label htmlFor="apiKey">Gemini API key</Label>
                  <Input
                    id="apiKey"
                    type="password"
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder="Paste your Gemini key"
                    autoComplete="off"
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="outputFilename">Output filename</Label>
                  <Input
                    id="outputFilename"
                    value={outputFilename}
                    onChange={(event) => setOutputFilename(event.target.value)}
                    placeholder="short_con_subs.mp4"
                  />
                </div>

                <div className="rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-white/65">
                  The backend accepts the Gemini recommendation automatically so the first web version stays fast and simple.
                </div>

                <Button className="w-full" type="submit" disabled={isSubmitting || isWorking}>
                  {isSubmitting || isWorking ? (
                    <>
                      <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> Processing locally
                    </>
                  ) : (
                    <>
                      <Video className="mr-2 h-4 w-4" /> Start job
                    </>
                  )}
                </Button>

                {requestError ? <p className="text-sm text-rose-300">{requestError}</p> : null}
              </form>
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card className="border-white/10 bg-white/5 backdrop-blur-xl">
              <CardHeader>
                <CardTitle className="text-white">Pipeline status</CardTitle>
                <CardDescription className="text-white/60">
                  The browser polls your local backend every two seconds while the job runs.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="flex items-center justify-between text-sm text-white/65">
                  <span>{job.message ?? 'No active job yet.'}</span>
                  <Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'}>{job.status}</Badge>
                </div>
                <Progress value={progressByStatus[job.status]} />
                <div className="grid gap-3 sm:grid-cols-2">
                  {labels.map((item) => {
                    const active = progressByStatus[job.status] >= progressByStatus[item.status]
                    return (
                      <div
                        key={item.status}
                        className={`rounded-2xl border px-4 py-3 text-sm transition ${
                          active
                            ? 'border-sand/60 bg-sand/10 text-white'
                            : 'border-white/10 bg-white/5 text-white/45'
                        }`}
                      >
                        {item.label}
                      </div>
                    )
                  })}
                </div>
                {job.error ? <p className="text-sm text-rose-300">{job.error}</p> : null}
              </CardContent>
            </Card>

            <Card className="border-white/10 bg-[#0f172fcc] backdrop-blur-xl">
              <CardHeader>
                <CardTitle className="text-white">What you get</CardTitle>
                <CardDescription className="text-white/60">
                  Once the job is done, download the rendered MP4 and the transcript from this panel.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {job.status === 'completed' && job.result && jobId ? (
                  <>
                    <div className="rounded-2xl border border-emerald-400/30 bg-emerald-400/10 p-4 text-sm text-emerald-100">
                      <div className="mb-2 flex items-center gap-2 font-medium">
                        <CheckCircle2 className="h-4 w-4" /> Render complete
                      </div>
                      <p>{job.result.title ?? 'Untitled clip'}</p>
                      <p className="mt-2 text-emerald-100/80">{job.result.reason ?? 'Gemini selected the strongest segment.'}</p>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                        <p className="text-xs uppercase tracking-[0.3em] text-white/45">Start</p>
                        <p className="mt-2 text-2xl font-semibold text-white">{job.result.start.toFixed(1)}s</p>
                      </div>
                      <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                        <p className="text-xs uppercase tracking-[0.3em] text-white/45">End</p>
                        <p className="mt-2 text-2xl font-semibold text-white">{job.result.end.toFixed(1)}s</p>
                      </div>
                    </div>

                    <div className="flex flex-col gap-3 sm:flex-row">
                      <Button asChild className="flex-1">
                        <a href={`/api/jobs/${jobId}/download/video`}>
                          <Download className="mr-2 h-4 w-4" /> Download MP4
                        </a>
                      </Button>
                      <Button asChild variant="secondary" className="flex-1">
                        <a href={`/api/jobs/${jobId}/download/transcript`}>
                          <Download className="mr-2 h-4 w-4" /> Download transcript
                        </a>
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="rounded-2xl border border-dashed border-white/15 bg-white/[0.03] p-5 text-sm text-white/50">
                    Start a job to see the selected timestamps, Gemini reasoning, and download actions here.
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
