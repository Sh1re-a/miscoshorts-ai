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
  queued: 'Queued',
  validating: 'Validating',
  downloading: 'Downloading',
  transcribing: 'Transcribing',
  analyzing: 'Analyzing',
  rendering: 'Rendering',
  completed: 'Render complete',
  failed: 'Render failed',
}

export const stageDescriptions: Record<JobStatus, string> = {
  idle: 'Paste a YouTube link, add your Gemini key, and start a render.',
  queued: 'Waiting for a worker slot or for an identical active render to finish.',
  validating: 'Checking subtitle compatibility and local requirements.',
  downloading: 'Downloading the source video.',
  transcribing: 'Transcribing audio with Whisper. This is usually the longest step.',
  analyzing: 'Gemini is selecting the strongest clip moments from the transcript.',
  rendering: 'Rendering the final vertical video with subtitles and overlays.',
  completed: 'Your clips are ready below.',
  failed: 'The job stopped before finishing. See the error details below.',
}
