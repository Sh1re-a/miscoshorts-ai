export type PreviewCue = {
  cue: number
  text: string
  start: number
  end: number
  width: number
  height: number
  position: [number, number]
  frames: Record<string, string>
}

export type SubtitlePreviewPayload = {
  previewId: string
  title: string
  reason: string
  subtitleStyle: {
    fontPreset: string
    colorPreset: string
  }
  videoSize: {
    width: number
    height: number
  }
  headerImages: string[]
  subtitleFrames: PreviewCue[]
}
