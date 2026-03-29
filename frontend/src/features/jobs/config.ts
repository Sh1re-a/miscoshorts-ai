import type { JobStatus } from './types'

export const feedbackTags = [
  { id: 'great_content', label: 'Great content', positive: true },
  { id: 'good_framing', label: 'Good framing', positive: true },
  { id: 'good_subtitles', label: 'Good subtitles', positive: true },
  { id: 'boring_content', label: 'Boring content', positive: false },
  { id: 'bad_crop', label: 'Bad crop', positive: false },
  { id: 'wrong_layout', label: 'Wrong layout', positive: false },
  { id: 'bad_subtitles', label: 'Bad subtitles', positive: false },
  { id: 'audio_issue', label: 'Audio issue', positive: false },
] as const

export const progressByStatus: Record<JobStatus, number> = {
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

export const statusTitles: Record<JobStatus, string> = {
  idle: 'Ready to start',
  queued: 'Waiting in the local queue',
  validating: 'Validating subtitle compatibility',
  downloading: 'Downloading source video',
  transcribing: 'Transcribing audio',
  analyzing: 'Gemini is selecting clips',
  rendering: 'Rendering video',
  completed: 'Render complete',
  failed: 'Render failed',
}

export const stageDescriptions: Record<JobStatus, string> = {
  idle: 'Paste a YouTube link, add your Gemini key, and start the default Shorts render flow.',
  queued: 'The backend accepted the request and is waiting either for a worker slot or for an identical active render to finish first.',
  validating: 'Checking subtitle rendering compatibility and local requirements before the heavy work starts.',
  downloading: 'Downloading the source video and preparing local files.',
  transcribing: 'Whisper is transcribing the video. This is usually the longest step.',
  analyzing: 'Gemini is selecting the strongest clip moments from the transcript.',
  rendering: 'Rendering the final vertical video with dynamic subtitles and overlays.',
  completed: 'Everything is finished. Your files are ready below as high-quality 1080x1920 exports.',
  failed: 'The job stopped before finishing. Read the message below for the exact reason.',
}
