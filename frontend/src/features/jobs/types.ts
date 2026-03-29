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
  renderMetrics?: Record<string, unknown>
}

export type RunSummary = {
  status?: string
  totalJobSeconds?: number
  clipCount?: number
  generatedClipCount?: number
  reusedClipCount?: number
  reusedExisting?: boolean
  cache?: Record<string, string>
  cacheHits?: string[]
  cacheMisses?: string[]
  slowestClip?: number | null
  slowestClipSeconds?: number | null
  slowestPhases?: Array<[string, number]>
  peakRssBytes?: number | null
  peakProcessRssBytes?: number | null
  peakWorkspaceBytes?: number | null
  finalOutputBytes?: number | null
  largestClipIndex?: number | null
  largestClipOutputBytes?: number | null
  cleanupSucceeded?: boolean | null
  promotionSucceeded?: boolean | null
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
  sourceMediaPresent?: boolean
  sourceMediaDeletedAt?: number
  metrics?: Record<string, unknown>
  runReportPath?: string
  runSummary?: RunSummary
  logPath?: string
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
  jobFingerprint?: string
  queueState?: 'queued' | 'waiting_for_worker' | 'waiting_for_identical_render' | 'running' | 'completed' | 'failed'
  waitingOnJobId?: string | null
  waitingOnFingerprint?: string | null
  runtimeSessionId?: string
}

export type BootstrapPayload = {
  hasConfiguredApiKey: boolean
  frontendBuilt: boolean
  defaultRenderProfile: string
  renderProfiles: Record<string, string>
  runtimeSessionId: string
  serverStartedAt: number
  speakerDiarizationMode: string
  hasPyannoteToken: boolean
  doctorStatus: string
  runtime: Record<string, string>
  logPath: string
  doctorReportPath?: string
  queue?: RuntimeQueueSnapshot
  recovery?: RuntimeRecoveryPayload
  consistency?: RuntimeConsistencyPayload
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
  jobFingerprint?: string
  queueState?: JobPayload['queueState']
  waitingOnJobId?: string | null
  runtimeSessionId?: string
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

export type RuntimeConsistencyPayload = {
  status: 'ok' | 'degraded'
  issues: string[]
}

export type RuntimeJobSummary = JobPayload & {
  jobId: string
}

export type RuntimeLockPayload = {
  path: string
  fingerprint: string
  jobId?: string | null
  pid?: number | null
  createdAt?: number | null
  ageSeconds: number
  alive: boolean
  payloadValid: boolean
  reason?: string
}

export type RuntimeRecoveryPayload = {
  recoveredAt?: number
  recoveredJobIds?: string[]
  clearedLocks?: RuntimeLockPayload[]
  activeLocks?: RuntimeLockPayload[]
  clearedTempWorkspacePaths?: string[]
}

export type RuntimeQueueSnapshot = {
  activeCount: number
  queuedCount: number
  waitingForWorkerCount: number
  waitingForIdenticalRenderCount: number
  activeJobs: RuntimeJobSummary[]
  queuedJobs: RuntimeJobSummary[]
}

export type RuntimePayload = {
  runtimeSessionId: string
  serverPid: number
  serverStartedAt: number
  backendSignature: string
  logPath: string
  runtime: Record<string, string>
  queue: RuntimeQueueSnapshot
  locks: RuntimeLockPayload[]
  recovery: RuntimeRecoveryPayload
  recentJobs: RuntimeJobSummary[]
  consistency: RuntimeConsistencyPayload
}

export type StorageBucketSummary = {
  path: string
  bytes: number
  sourceMediaBytes?: number
  sourceMediaFiles?: number
  transcriptBytes?: number
  transcriptFiles?: number
  clipAnalysisBytes?: number
  clipAnalysisFiles?: number
  otherBytes?: number
}

export type StorageManageableJob = {
  jobId: string
  status: 'completed' | 'failed'
  updatedAt?: number
  videoUrl?: string | null
  jobFingerprint?: string | null
  outputDir?: string | null
  outputExists: boolean
  sharedOutputRefs: number
  canDeleteJob: boolean
  canDeleteSourceMedia: boolean
  canDeleteStateOnly: boolean
  storage: {
    clipsBytes: number
    sourceMediaBytes: number
    diagnosticsBytes: number
    metadataBytes: number
    outputBytes: number
    sourceCacheBytes: number
  }
}

export type StorageReportPayload = {
  summary: Record<string, StorageBucketSummary>
  jobStateCounts: {
    active: number
    queued: number
    completed: number
    failed: number
  }
  manageableJobs: StorageManageableJob[]
  recommendations: {
    canPruneTemp: boolean
    canPruneCache: boolean
    canCleanFinishedJobs: boolean
    canDeleteJobSourceMedia: boolean
  }
}

export type StorageActionResponse = {
  storage: StorageReportPayload
  jobId?: string
  mode?: string
  removedItems?: number
  removedBytes?: number
  failedJobs?: {
    removedItems: number
    removedBytes: number
    removedJobIds: string[]
  }
}
