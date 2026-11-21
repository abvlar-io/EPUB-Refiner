import type { AgentEvent } from "../types";

interface TimelinePanelProps {
  events: AgentEvent[];
  busy: boolean;
}

export function TimelinePanel({ events, busy }: TimelinePanelProps) {
  return (
    <aside className="timeline-panel">
      <div className="panel-title-row">
        <h3>处理进度记录</h3>
        {busy && <span className="pulse-tag">处理中…</span>}
      </div>

      {events.length === 0 ? (
        <p className="timeline-empty">
          拖入一本或多本 `.txt` / `.epub` 电子书后，这里会实时展示文字识别、章节拆分与规范质检的每一步过程。
        </p>
      ) : (
        <ul className="timeline-list">
          {events.map((ev, i) => (
            <li key={`${ev.timestamp}-${i}`} className={`timeline-item level-${ev.level}`}>
              <div className="tl-dot" />
              <div className="tl-body">
                <div className="tl-head">
                  <span className="tl-step">{ev.step}</span>
                  <span className="tl-time">
                    {new Date(ev.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}
                  </span>
                </div>
                <p className="tl-msg">{ev.message}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
