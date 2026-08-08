/* eslint-disable react-refresh/only-export-components */
import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowUp,
  BookOpen,
  Check,
  CircleAlert,
  Clock3,
  ExternalLink,
  Github,
  LoaderCircle,
  MessageSquare,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Citation = {
  source_id: string;
  project: string;
  title: string;
  summary: string;
  url: string | null;
};
type RunStatus =
  "queued" | "running" | "completed" | "failed" | "interrupted" | "expired";
type Run = {
  run_id: string;
  status: RunStatus;
  answer: { text: string; citations: Citation[] } | null;
  error_message: string | null;
};
type Message = { id: string; role: "user" | "assistant"; text: string };
type Profile = {
  name: string;
  monogram: string;
  role: string;
  github: string | null;
  greeting: string;
  style: string;
  covered_topics: string[];
};
type TopicItem = {
  source_id: string;
  title: string;
  summary: string;
  url: string | null;
  questions: string[];
};
type TopicGroup = { project: string; topics: TopicItem[] };
type SuggestedQuestion = { question: string; project: string | null };
type AnswerBlock = {
  kind: "paragraph" | "heading" | "list";
  content?: string;
  items?: string[];
};

const fallbackProfile: Profile = {
  name: "AI 数字分身",
  monogram: "AI",
  role: "",
  github: null,
  greeting: "从知识开始，了解我的工程实践。",
  style: "",
  covered_topics: [],
};
const fallbackQuestions = [
  "你能介绍哪些项目？",
  "你的知识覆盖哪些主题？",
  "你如何保证回答不编造？",
];
const sessionKey = "distill-agent-conversation-id";
const apiPrefix = window.location.pathname.startsWith("/agent") ? "/agent" : "";

function apiUrl(url: string) {
  return apiPrefix + url;
}

function conversationId() {
  const current = sessionStorage.getItem(sessionKey);
  if (current) return current;
  const next = crypto.randomUUID();
  sessionStorage.setItem(sessionKey, next);
  return next;
}
function statusLabel(status: RunStatus) {
  return {
    queued: "已进入队列",
    running: "正在检索与组织回答",
    completed: "回答完成",
    failed: "处理失败",
    interrupted: "任务中断",
    expired: "任务过期",
  }[status];
}
function collectQuestions(
  groups: TopicGroup[],
  limit = 8,
): SuggestedQuestion[] {
  const result: SuggestedQuestion[] = [];
  for (const group of groups)
    for (const topic of group.topics)
      for (const question of topic.questions) {
        result.push({ question, project: group.project });
        if (result.length >= limit) return result;
      }
  return result;
}
async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(url), options);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? "请求失败 (" + response.status + ")");
  }
  return response.json() as Promise<T>;
}
function inlineText(text: string) {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .map((part, index) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong key={index}>{part.slice(2, -2)}</strong>
      ) : (
        part
      ),
    );
}
function answerBlocks(text: string): AnswerBlock[] {
  const blocks: AnswerBlock[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ kind: "paragraph", content: paragraph.join(" ") });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push({ kind: "list", items: list });
      list = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }
    if (/^#{1,3}\s/.test(line)) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "heading", content: line.replace(/^#{1,3}\s+/, "") });
      continue;
    }
    const item = line.match(/^(?:[-*]|\d+[.)])\s+(.+)$/);
    if (item) {
      flushParagraph();
      list.push(item[1]);
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushParagraph();
  flushList();
  return blocks;
}
function AnswerContent({ text }: { text: string }) {
  return (
    <div className="answer-content">
      {answerBlocks(text).map((block, index) =>
        block.kind === "heading" ? (
          <h3 key={index}>{inlineText(block.content ?? "")}</h3>
        ) : block.kind === "list" ? (
          <ul key={index}>
            {block.items?.map((item, itemIndex) => (
              <li key={itemIndex}>{inlineText(item)}</li>
            ))}
          </ul>
        ) : (
          <p key={index}>{inlineText(block.content ?? "")}</p>
        ),
      )}
    </div>
  );
}

function StatusLine({ status }: { status: RunStatus }) {
  const icon =
    status === "completed" ? (
      <Check size={13} />
    ) : status === "failed" ||
      status === "expired" ||
      status === "interrupted" ? (
      <X size={13} />
    ) : (
      <LoaderCircle className="spin" size={13} />
    );
  return (
    <div className={"run-status " + status}>
      <span>{icon}</span>
      <strong>{statusLabel(status)}</strong>
      <span className="mono">
        {status === "running" ? "RETRIEVAL / ANSWER" : status.toUpperCase()}
      </span>
    </div>
  );
}

function SourcesPanel({
  citations,
  hasAnswer,
}: {
  citations: Citation[];
  hasAnswer: boolean;
}) {
  return (
    <aside className="sources-panel">
      <div className="panel-heading">
        <span>
          <BookOpen size={15} />
          公开来源
        </span>
        <span className="mono">
          {citations.length.toString().padStart(2, "0")}
        </span>
      </div>
      {citations.length === 0 ? (
        <div className="sources-empty">
          <Search size={18} />
          <p>
            {hasAnswer
              ? "本次回答没有可展示的公开引用。"
              : "完成一次提问后，这里会列出回答所依据的公开资料。"}
          </p>
        </div>
      ) : (
        <div className="source-list">
          {citations.map((citation) => (
            <article className="source-card" key={citation.source_id}>
              <div className="source-meta">
                <span>{citation.project}</span>
                <code>{citation.source_id}</code>
              </div>
              <h3>{citation.title}</h3>
              <p>{citation.summary}</p>
              {citation.url && (
                <a href={citation.url} target="_blank" rel="noreferrer">
                  查看公开资料 <ExternalLink size={13} />
                </a>
              )}
            </article>
          ))}
        </div>
      )}
      <div className="source-note">
        <ShieldCheck size={15} />
        <span>只展示经批准的公开元数据，不暴露私有文档路径。</span>
      </div>
    </aside>
  );
}

function App() {
  const [profile, setProfile] = useState<Profile>(fallbackProfile);
  const [topics, setTopics] = useState<TopicGroup[]>([]);
  const [suggested, setSuggested] = useState<SuggestedQuestion[]>(
    fallbackQuestions.map((question) => ({ question, project: null })),
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    void fetch(apiUrl("/api/profile"))
      .then((response) => (response.ok ? response.json() : null))
      .then((body: Profile | null) => {
        if (body) setProfile(body);
      })
      .catch(() => undefined);
    void fetch(apiUrl("/api/topics"))
      .then((response) => (response.ok ? response.json() : []))
      .then((groups: TopicGroup[]) => {
        setTopics(groups);
        const questions = collectQuestions(groups);
        if (questions.length) setSuggested(questions);
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);
  async function pollRun(runId: string) {
    for (;;) {
      const run = await requestJson<Run>("/api/runs/" + runId);
      setRunStatus(run.status);
      if (
        ["completed", "failed", "interrupted", "expired"].includes(run.status)
      )
        return run;
      await new Promise((resolve) => window.setTimeout(resolve, 700));
    }
  }
  async function submit(question: string) {
    const text = question.trim();
    if (!text || pending) return;
    setDraft("");
    setError(null);
    setCitations([]);
    setPending(true);
    setRunStatus("queued");
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", text },
    ]);
    try {
      const run = await requestJson<Run>(
        "/api/conversations/" + conversationId() + "/messages",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: text }),
        },
      );
      const finished = await pollRun(run.run_id);
      if (finished.status !== "completed" || !finished.answer)
        throw new Error(finished.error_message ?? statusLabel(finished.status));
      setCitations(finished.answer.citations);
      setMessages((current) => [
        ...current,
        { id: finished.run_id, role: "assistant", text: finished.answer!.text },
      ]);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "请求失败，请稍后重试",
      );
      setRunStatus("failed");
    } finally {
      setPending(false);
    }
  }
  function resetConversation() {
    sessionStorage.removeItem(sessionKey);
    conversationId();
    setMessages([]);
    setCitations([]);
    setError(null);
    setRunStatus(null);
  }
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit(draft);
  }
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit(draft);
    }
  }
  const sourceCount = useMemo(
    () => topics.reduce((sum, group) => sum + group.topics.length, 0),
    [topics],
  );
  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="identity" href="#chat">
          <span className="monogram">{profile.monogram}</span>
          <span>
            <strong>{profile.name}</strong>
            <small>PERSONAL KNOWLEDGE AGENT</small>
          </span>
        </a>
        <div className="topbar-meta">
          <span className="live-dot" />
          <span className="mono">KNOWLEDGE READY</span>
          <span className="topbar-divider" />
          {profile.github && (
            <a
              href={profile.github}
              target="_blank"
              rel="noreferrer"
              title="GitHub"
            >
              <Github size={17} />
            </a>
          )}
          <button
            className="icon-button"
            type="button"
            onClick={resetConversation}
            disabled={pending}
            title="开始新的临时对话"
          >
            <RotateCcw size={17} />
          </button>
        </div>
      </header>
      <div className="workspace">
        <aside className="knowledge-panel">
          <div className="panel-heading">
            <span>
              <Sparkles size={15} />
              知识范围
            </span>
            <span className="status-chip">
              <span className="live-dot" />
              READY
            </span>
          </div>
          <div className="knowledge-intro">
            <div className="orb">
              <span>{profile.monogram}</span>
              <i />
              <i />
              <i />
            </div>
            <p className="eyebrow">AUTHORISED KNOWLEDGE</p>
            <h1>
              {profile.greeting ||
                "你好，我是 " + profile.name + " 的 AI 数字分身。"}
            </h1>
            <p>
              {profile.role
                ? "围绕" + profile.role + "与相关工程实践回答问题。"
                : "基于授权项目资料回答工程与项目问题。"}
            </p>
          </div>
          {profile.covered_topics.length > 0 && (
            <div className="topic-tags">
              {profile.covered_topics.map((topic) => (
                <span key={topic}>{topic}</span>
              ))}
            </div>
          )}
          <div className="knowledge-stats">
            <div>
              <strong>{sourceCount || "—"}</strong>
              <span>PUBLIC SOURCES</span>
            </div>
            <div>
              <strong>{profile.covered_topics.length || "—"}</strong>
              <span>TOPICS</span>
            </div>
          </div>
          <div className="suggested">
            <div className="subheading">
              <MessageSquare size={14} />
              可以这样问
            </div>
            {suggested.map((item) => (
              <button
                key={item.question}
                type="button"
                onClick={() => void submit(item.question)}
                disabled={pending}
              >
                {item.question}
                <ArrowUp size={13} />
              </button>
            ))}
          </div>
          <div className="disclosure">
            <ShieldCheck size={15} />
            <span>AI 生成回答，资料未覆盖时会明确说明。</span>
          </div>
        </aside>
        <section className="conversation" id="chat" aria-label="对话">
          <div className="conversation-header">
            <div>
              <p className="eyebrow">LIVE CONVERSATION</p>
              <h2>和知识库对话</h2>
            </div>
            <span className="mono">SESSION / TEMPORARY</span>
          </div>
          {runStatus && <StatusLine status={runStatus} />}
          {messages.length === 0 && !pending && (
            <div className="empty-state">
              <div className="empty-grid">
                <span />
                <span />
                <span />
                <span />
              </div>
              <h3>从一个项目问题开始</h3>
              <p>
                可以问系统架构、Agent 设计、工程取舍，或任何公开资料中的内容。
              </p>
            </div>
          )}
          <div className="messages">
            {messages.map((message) => (
              <article className={"message " + message.role} key={message.id}>
                <div className="message-label">
                  {message.role === "user"
                    ? "YOU"
                    : profile.name.toUpperCase() + " / AGENT"}
                </div>
                {message.role === "assistant" ? (
                  <AnswerContent text={message.text} />
                ) : (
                  <p>{message.text}</p>
                )}
              </article>
            ))}
            {pending && (
              <article className="message assistant pending">
                <div className="message-label">
                  {profile.name.toUpperCase()} / AGENT
                </div>
                <p>
                  <LoaderCircle className="spin" size={16} />
                  正在根据授权资料组织回答…
                </p>
              </article>
            )}
            {error && (
              <div className="error-notice" role="alert">
                <CircleAlert size={16} />
                {error}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
          <form className="composer" onSubmit={onSubmit}>
            <label className="sr-only" htmlFor="question">
              提问
            </label>
            <textarea
              id="question"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder="问我一个项目或工程问题…"
              maxLength={2000}
              rows={1}
              disabled={pending}
            />
            <span className="composer-hint mono">
              ENTER 发送 · SHIFT+ENTER 换行
            </span>
            <button
              type="submit"
              aria-label="发送问题"
              disabled={pending || !draft.trim()}
            >
              <ArrowUp size={19} />
            </button>
          </form>
          <p className="retention">
            <Clock3 size={13} />
            此标签页的临时对话会保留至多 24 小时。
          </p>
        </section>
        <SourcesPanel
          citations={citations}
          hasAnswer={messages.some((message) => message.role === "assistant")}
        />
      </div>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
