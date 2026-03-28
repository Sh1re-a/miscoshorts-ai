export type JobStatus =
  | 'idle'
  | 'queued'
  | 'validating'
  | 'downloading'
  | 'transcribing'
  | 'analyzing'
  | 'rendering'
  | 'completed'
  | 'failed'

export type JobLog = {
  time: number
  stage: string
  message: string
}

export type JobClip = {
  index: number
  title?: string
  reason?: string
  start: number
  end: number
  outputFilename: string
  contentType?: string
  analytics?: Record<string, unknown>
  subtitlePlanPath?: string | null
  subtitlePreflightPath?: string | null
  subtitleCueCount?: number
  subtitlePreflightWarnings?: number
}

export type JobResult = {
  jobId?: string
  jobFingerprint?: string
  videoUrl?: string
  title?: string
  reason?: string
  start: number
  end: number
  outputFilename: string
  outputPath?: string
  transcriptPath?: string
  sourceMetaPath?: string
  sourceDownload?: Record<string, unknown>
  subtitleStyle?: string
  outputDir: string
  clipCount: number
  renderProfile?: string
  renderProfileKey?: string
  generatedAt?: number
  lastUsedAt?: number
  reusedExisting?: boolean
  clips: JobClip[]
}

export type ClipFeedback = {
  rating: 'good' | 'bad'
  tags: string[]
  saving?: boolean
  saved?: boolean
}

export type AnalyticsInsights = {
  totalClips: number
  totalRated: number
  totalGood: number
  totalBad: number
  overallApprovalRate: number | null
  perContentType: Record<string, {
    clipCount: number
    avgConfidence: number | null
    rated: number
    good: number
    bad: number
    approvalRate: number | null
  }>
}

export type JobPayload = {
  status: JobStatus
  message?: string
  error?: string
  errorHelp?: string
  errorCategory?: string
  errorId?: string
  logs?: JobLog[]
  result?: JobResult
  clipCount?: number
  queuePosition?: number
  renderProfile?: string
  overallProgress?: number
  stageProgress?: number
  etaSeconds?: number | null
  createdAt?: number
  updatedAt?: number
}

export type BootstrapPayload = {
  hasConfiguredApiKey: boolean
  frontendBuilt: boolean
  defaultRenderProfile: string
  renderProfiles: Record<string, string>
  speakerDiarizationMode: string
  hasPyannoteToken: boolean
  doctorStatus: string
  runtime: Record<string, string>
  logPath: string
  doctorReportPath?: string
}

export type ProcessErrorPayload = {
  error?: string
  errorHelp?: string
  doctorStatus?: string
  doctorReportPath?: string
  blockingChecks?: DoctorCheck[]
  details?: string
  jobId?: string
  status?: JobStatus
  clipCount?: number
  queuePosition?: number
  renderProfile?: string
}

export type DoctorCheck = {
  status: 'PASS' | 'WARN' | 'FAIL'
  name: string
  message: string
  fix?: string | null
}

export type DoctorReport = {
  status: 'PASS' | 'WARN' | 'FAIL'
  checks: DoctorCheck[]
  paths: Record<string, string>
  storage?: Record<string, { path: string; bytes: number }>
  logPath: string
  reportPath: string
  whisper: {
    backendMode: string
    requestedModels: string[]
    configuredValue: string
    cacheSizeBytes: number
  }
}
