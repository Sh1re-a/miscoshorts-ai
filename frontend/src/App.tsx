import { useEffect, useEffectEvent, useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, Download, LoaderCircle, PlaySquare, Sparkles, Type, WandSparkles } from 'lucide-react'

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
  subtitleStyle?: SubtitleStyle
}

type JobPayload = {
  status: JobStatus
  message?: string
  error?: string
  result?: JobResult
}

type SubtitleStyle = {
  fontPreset: FontPreset
  colorPreset: ColorPreset
  fontSize: number
}

type FontPreset = 'clean' | 'bold' | 'soft'
type ColorPreset = 'sun' | 'ivory' | 'mint'

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

const fontPresets: Array<{ id: FontPreset; label: string; stack: string; note: string }> = [
  { id: 'clean', label: 'Studio', stack: 'Avenir Next, Helvetica Neue, Arial, sans-serif', note: 'Premium and balanced' },
  { id: 'bold', label: 'Punch', stack: 'Avenir Next Condensed, Arial Black, sans-serif', note: 'Stronger impact' },
  { id: 'soft', label: 'Editorial', stack: 'Trebuchet MS, Gill Sans, Arial, sans-serif', note: 'Softer and polished' },
]

const colorPresets: Array<{ id: ColorPreset; label: string; text: string; stroke: string }> = [
  { id: 'sun', label: 'Sun', text: '#f6d34a', stroke: '#101010' },
  { id: 'ivory', label: 'Ivory', text: '#fff7e8', stroke: '#101010' },
  { id: 'mint', label: 'Mint', text: '#d8fff3', stroke: '#102a43' },
]

const defaultSubtitleStyle: SubtitleStyle = {
  fontPreset: 'clean',
  colorPreset: 'sun',
  fontSize: 35,
}

function App() {
  const [videoUrl, setVideoUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [outputFilename, setOutputFilename] = useState('short_con_subs.mp4')
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyle>(defaultSubtitleStyle)
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobPayload>({ status: 'idle' })
  const [requestError, setRequestError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const activeFontPreset = fontPresets.find((preset) => preset.id === subtitleStyle.fontPreset) ?? fontPresets[0]
  const activeColorPreset = colorPresets.find((preset) => preset.id === subtitleStyle.colorPreset) ?? colorPresets[0]

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
    setJob({ status: 'queued', message: 'Preparing your local render...' })
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
          subtitleStyle,
        }),
      })

      const payload = (await response.json()) as { jobId?: string; error?: string; status?: JobStatus }

      if (!response.ok || !payload.jobId) {
        throw new Error(payload.error ?? 'Could not start the job.')
      }

      setJobId(payload.jobId)
      setJob({ status: payload.status ?? 'queued', message: 'The job started in your local backend.' })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unexpected error'
      setJob({ status: 'failed', error: message })
      setRequestError(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <main className="min-h-screen bg-porcelain text-slate-900">
      <div className="absolute inset-0 -z-10 overflow-hidden">
        <div className="hero-orb hero-orb-left" />
        <div className="hero-orb hero-orb-right" />
        <div className="grid-fade" />
      </div>

      <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-8 px-5 py-6 lg:px-10 lg:py-10">
        <header className="grid gap-5 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
          <div className="max-w-3xl space-y-3">
            <Badge variant="outline" className="border-amber-300/70 bg-white/75 text-amber-900 backdrop-blur">
              Light Studio Mode
            </Badge>
            <h1 className="max-w-3xl text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl lg:text-6xl">
              Make a short fast. Preview the subtitle look before you render.
            </h1>
            <p className="max-w-2xl text-base text-slate-600 sm:text-lg">
              Paste a link, choose a subtitle style, and export from one clean local workflow.
            </p>
          </div>

          <Card className="w-full border-white/60 bg-white/75 shadow-[0_24px_70px_rgba(195,164,121,0.16)] backdrop-blur-xl">
            <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <CardTitle className="flex items-center gap-2 text-lg text-slate-900">
                  <Sparkles className="h-5 w-5 text-amber-600" /> Local mode
                </CardTitle>
                <CardDescription className="text-slate-600">
                  React in the browser. Python on your machine.
                </CardDescription>
              </div>
              <div className="flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1 text-sm text-emerald-700">
                <span className="h-2 w-2 rounded-full bg-emerald-500" /> Ready
              </div>
            </CardHeader>
          </Card>
        </header>

        <section className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
          <div className="space-y-6">
            <Card className="border-white/65 bg-white/82 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <CardHeader>
                <CardTitle className="text-2xl text-slate-950">Create</CardTitle>
                <CardDescription className="text-slate-600">
                  Three fields. One render.
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
                    <Label htmlFor="outputFilename">Output file</Label>
                    <Input
                      id="outputFilename"
                      value={outputFilename}
                      onChange={(event) => setOutputFilename(event.target.value)}
                      placeholder="short_con_subs.mp4"
                    />
                  </div>

                  <div className="rounded-[24px] border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-slate-700 shadow-sm shadow-amber-100/70">
                    The first browser version keeps clip selection automatic. You style the subtitles, then Gemini picks the moment.
                  </div>

                  <Button className="w-full" type="submit" disabled={isSubmitting || isWorking}>
                    {isSubmitting || isWorking ? (
                      <>
                        <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> Rendering locally
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

            <Card className="border-white/65 bg-white/82 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-xl text-slate-950">
                  <Type className="h-5 w-5 text-amber-600" /> Subtitle style
                </CardTitle>
                <CardDescription className="text-slate-600">
                  Keep it simple. Pick a font feel, a color, and a size.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="space-y-3">
                  <Label>Font feel</Label>
                  <div className="grid gap-3 sm:grid-cols-3">
                    {fontPresets.map((preset) => {
                      const selected = subtitleStyle.fontPreset === preset.id
                      return (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => setSubtitleStyle((current) => ({ ...current, fontPreset: preset.id }))}
                          className={`rounded-[22px] border px-4 py-4 text-left transition ${
                            selected
                              ? 'border-amber-300 bg-amber-50 shadow-[0_14px_30px_rgba(245,158,11,0.14)]'
                              : 'border-slate-200 bg-white hover:border-amber-200 hover:bg-amber-50/40'
                          }`}
                        >
                          <p className="text-base font-semibold text-slate-900">{preset.label}</p>
                          <p className="mt-1 text-sm text-slate-500">{preset.note}</p>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="space-y-3">
                  <Label>Color</Label>
                  <div className="flex flex-wrap gap-3">
                    {colorPresets.map((preset) => {
                      const selected = subtitleStyle.colorPreset === preset.id
                      return (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => setSubtitleStyle((current) => ({ ...current, colorPreset: preset.id }))}
                          className={`flex items-center gap-3 rounded-full border px-4 py-2 transition ${
                            selected ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-700'
                          }`}
                        >
                          <span className="h-4 w-4 rounded-full border border-black/10" style={{ backgroundColor: preset.text }} />
                          {preset.label}
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="fontSize">Size</Label>
                    <span className="text-sm text-slate-500">{subtitleStyle.fontSize}px</span>
                  </div>
                  <input
                    id="fontSize"
                    type="range"
                    min={24}
                    max={56}
                    step={2}
                    value={subtitleStyle.fontSize}
                    onChange={(event) =>
                      setSubtitleStyle((current) => ({ ...current, fontSize: Number(event.target.value) }))
                    }
                    className="slider w-full"
                  />
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="space-y-6">
            <Card className="overflow-hidden border-white/65 bg-white/82 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <CardHeader className="sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle className="text-xl text-slate-950">Live subtitle preview</CardTitle>
                  <CardDescription className="text-slate-600">
                    This style is sent with the render job.
                  </CardDescription>
                </div>
                <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
                  <WandSparkles className="mr-1 h-3.5 w-3.5" /> Preview
                </Badge>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="preview-stage aspect-[16/10] rounded-[28px] border border-white/70 p-4 shadow-inner shadow-white/50">
                  <div className="preview-video-frame">
                    <div className="preview-video-overlay" />
                    <div className="preview-kicker">Sample frame</div>
                      <div className="preview-subtitle-shadow" />
                    <div className="preview-subtitle-wrap">
                      <p
                        className="preview-subtitle"
                        style={{
                          fontFamily: activeFontPreset.stack,
                            fontSize: `${Math.max(20, subtitleStyle.fontSize * 0.78)}px`,
                          color: activeColorPreset.text,
                          WebkitTextStroke: `3px ${activeColorPreset.stroke}`,
                        }}
                      >
                        This is how your subtitle styling will look in the final short.
                      </p>
                    </div>
                  </div>
                </div>

                <div className="grid gap-3 md:grid-cols-3">
                  <div className="rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-3">
                    <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Font</p>
                    <p className="mt-2 font-semibold text-slate-900">{activeFontPreset.label}</p>
                  </div>
                  <div className="rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-3">
                    <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Color</p>
                    <p className="mt-2 font-semibold text-slate-900">{activeColorPreset.label}</p>
                  </div>
                  <div className="rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-3">
                    <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Size</p>
                    <p className="mt-2 font-semibold text-slate-900">{subtitleStyle.fontSize}px</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="border-white/65 bg-white/82 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <CardHeader>
                <CardTitle className="text-xl text-slate-950">Render status</CardTitle>
                <CardDescription className="text-slate-600">Your browser polls the local backend while the job runs.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="flex items-center justify-between gap-4 text-sm text-slate-600">
                  <span>{job.message ?? 'No active job yet.'}</span>
                  <Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'}>{job.status}</Badge>
                </div>
                <Progress value={progressByStatus[job.status]} />
                <div className="grid gap-3 sm:grid-cols-3">
                  {labels.map((item) => {
                    const active = progressByStatus[job.status] >= progressByStatus[item.status]
                    return (
                      <div
                        key={item.status}
                        className={`rounded-[22px] border px-4 py-3 text-sm transition ${
                          active
                            ? 'border-amber-300 bg-amber-50 text-slate-900'
                            : 'border-slate-200 bg-slate-50 text-slate-500'
                        }`}
                      >
                        {item.label}
                      </div>
                    )
                  })}
                </div>
                {job.error ? <p className="text-sm text-rose-600">{job.error}</p> : null}
              </CardContent>
            </Card>

            <Card className="border-white/65 bg-white/82 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <CardHeader>
                <CardTitle className="text-xl text-slate-950">Downloads</CardTitle>
                <CardDescription className="text-slate-600">Get the MP4 and transcript when the render is done.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {job.status === 'completed' && job.result && jobId ? (
                  <>
                    <div className="rounded-[24px] border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
                      <div className="mb-2 flex items-center gap-2 font-medium">
                        <CheckCircle2 className="h-4 w-4" /> Render complete
                      </div>
                      <p>{job.result.title ?? 'Untitled clip'}</p>
                      <p className="mt-2 text-emerald-800/80">{job.result.reason ?? 'Gemini selected the strongest moment.'}</p>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                        <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Start</p>
                        <p className="mt-2 text-2xl font-semibold text-slate-950">{job.result.start.toFixed(1)}s</p>
                      </div>
                      <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                        <p className="text-xs uppercase tracking-[0.3em] text-slate-500">End</p>
                        <p className="mt-2 text-2xl font-semibold text-slate-950">{job.result.end.toFixed(1)}s</p>
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
                  <div className="rounded-[24px] border border-dashed border-slate-300 bg-slate-50 p-5 text-sm text-slate-500">
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
