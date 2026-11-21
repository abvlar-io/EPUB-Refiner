export type TaskState =
  | "created"
  | "diagnosing"
  | "awaiting_confirm"
  | "processing"
  | "verifying"
  | "repairing"
  | "completed"
  | "failed";

export interface AgentEvent {
  timestamp: string;
  step: string;
  message: string;
  level: "info" | "success" | "warning" | "error";
}

export interface EpubCheckMessage {
  id: string;
  severity: string;
  message: string;
  file_path: string;
  line: number | null;
  column: number | null;
}

export interface EpubCheckResult {
  passed: boolean;
  skipped?: boolean;
  skip_reason?: string;
  fatal_count: number;
  error_count: number;
  warning_count: number;
  messages: EpubCheckMessage[];
}

export interface ChapterPreview {
  index: number;
  volume: string | null;
  title: string;
  char_count: number;
  excerpt: string;
}

export type StylePreset = "classic" | "modern" | "comfort";
export type WorkMode = "txt_only" | "txt_to_epub" | "epub_refine";

export interface TxtRules {
  title: string;
  author: string;
  language: string;
  volume_regex: string;
  chapter_regex: string;
  ad_regexes: string[];
  style_preset?: StylePreset;
}

export interface TxtDiagnosisReport {
  source_path: string;
  file_size_bytes: number;
  detected_encoding: string;
  mojibake_repaired: boolean;
  sample_text_preview: string;
  total_lines: number;
  total_chars: number;
  used_llm: boolean;
  llm_note: string;
  rules: TxtRules;
  chapter_count: number;
  warnings: string[];
  previews: ChapterPreview[];
}

export interface EpubIssue {
  code: string;
  label: string;
  severity: "error" | "warning" | "info";
  auto_fixable: boolean;
  description: string;
}

export interface EpubDiagnosisReport {
  source_path: string;
  file_size_bytes: number;
  has_drm: boolean;
  title: string;
  author: string;
  language: string;
  has_cover: boolean;
  spine_count: number;
  toc_count: number;
  initial_check: EpubCheckResult;
  issues: EpubIssue[];
}

export interface TaskRecord {
  task_id: string;
  kind: "txt" | "epub";
  state: TaskState;
  source_path: string;
  output_path: string | null;
  utf8_txt_output_path: string | null;
  events: AgentEvent[];
  txt_report: TxtDiagnosisReport | null;
  epub_report: EpubDiagnosisReport | null;
  final_check: EpubCheckResult | null;
  error: string | null;
}

export interface EngineConfig {
  baseUrl: string;
  sessionToken: string;
}

export interface OutputDirSettings {
  output_dir: string;
  is_custom: boolean;
  default_dir: string;
}

export interface EngineInfo {
  engine: string;
  version: string;
  python: string;
  supported_extensions: string[];
  epubcheck_configured: boolean;
  epubcheck_note?: string;
  deepseek_configured: boolean;
  llm_provider?: string;
  llm_model?: string;
  output_dir?: string;
  output_dir_is_custom?: boolean;
  default_output_dir?: string;
}

export interface LlmStatus {
  configured: boolean;
  masked: string;
  provider: string;
  model: string;
  base_url: string;
}

export interface BatchItem {
  id: string;
  filePath: string;
  fileName: string;
  status: "queued" | "diagnosing" | "processing" | "done" | "error";
  outputPath?: string;
  summary?: string;
  error?: string;
}
