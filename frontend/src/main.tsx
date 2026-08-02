import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowUp, Bot, ExternalLink, Github, LoaderCircle, RotateCcw, Sparkles } from 'lucide-react'
import './styles.css'

type Citation = { source_id: string; project: string; title: string; summary: string; url: string | null }
type Answer = { text: string; citations: Citation[] }
type Run = { run_id: string; status: RunStatus; answer: Answer | null; error_message: string | null }
type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'interrupted' | 'expired'
type Message = { id: string; role: 'user' | 'assistant'; text: string; citations?: Citation[]; status?: RunStatus }
type Profile = {
  name: string; monogram: string; role: string; github: string | null
  greeting: string; style: string; covered_topics: string[]
}
type TopicItem = { source_id: string; title: string; summary: string; url: string | null; questions: string[] }
type TopicGroup = { project: string; topics: TopicItem[] }

// 兜底内容：仅当 /api/profile 或 /api/topics 不可用（未建索引/接口未配置）时使用，
// 保证空知识库状态下页面依然可演示，不写死任何个人身份信息。
const fallbackProfile: Profile = {
  name: 'AI 数字分身', monogram: 'AI', role: '', github: null,
  greeting: '你好，我是基于授权资料构建的 AI 数字分身。', style: '', covered_topics: [],
}
const fallbackQuestions = ['你能介绍哪些项目？', '你的知识覆盖哪些主题？', '你如何保证回答不编造？']
const sessionKey = 'personal-agent-conversation-id'

function conversationId(): string {
  const existing = sessionStorage.getItem(sessionKey)
  if (existing) return existing
  const next = crypto.randomUUID()
  sessionStorage.setItem(sessionKey, next)
  return next
}

function statusText(status: RunStatus): string {
  return status === 'queued' ? '正在排队' : status === 'running' ? '正在检索与组织回答' : '请求未完成'
}

async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options)
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `请求失败 (${response.status})`)
  }
  return response.json() as Promise<T>
}

type SuggestedQuestion = { question: string; project: string | null }

function collectQuestions(groups: TopicGroup[], limit = 6): SuggestedQuestion[] {
  const result: SuggestedQuestion[] = []
  for (const group of groups) {
    for (const topic of group.topics) {
      for (const question of topic.questions) {
        result.push({ question, project: group.project })
        if (result.length >= limit) return result
      }
    }
  }
  return result
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [profile, setProfile] = useState<Profile>(fallbackProfile)
  const [suggested, setSuggested] = useState<SuggestedQuestion[]>(() =>
    fallbackQuestions.map((question) => ({ question, project: null })),
  )
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void fetch('/api/profile')
      .then((response) => (response.ok ? response.json() : null))
      .then((body: Profile | null) => { if (body) setProfile(body) })
      .catch(() => { /* 保持兜底身份 */ })
    void fetch('/api/topics')
      .then((response) => (response.ok ? response.json() : []))
      .then((groups: TopicGroup[]) => {
        const questions = collectQuestions(groups)
        if (questions.length > 0) setSuggested(questions)
      })
      .catch(() => { /* 保持兜底问题 */ })
  }, [])

  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), [messages, pending])

  async function pollRun(runId: string): Promise<Run> {
    for (;;) {
      const run = await requestJson<Run>(`/api/runs/${runId}`)
      if (run.status === 'completed' || run.status === 'failed' || run.status === 'interrupted' || run.status === 'expired') {
        return run
      }
      await new Promise((resolve) => window.setTimeout(resolve, 700))
    }
  }

  async function submit(question: string) {
    const text = question.trim()
    if (!text || pending) return
    setError(null)
    setDraft('')
    const userMessage: Message = { id: crypto.randomUUID(), role: 'user', text }
    setMessages((current) => [...current, userMessage])
    setPending(true)
    try {
      const run = await requestJson<Run>(`/api/conversations/${conversationId()}/messages`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: text }),
      })
      const finished = await pollRun(run.run_id)
      if (finished.status !== 'completed' || !finished.answer) throw new Error(finished.error_message ?? statusText(finished.status))
      const answer = finished.answer
      setMessages((current) => [...current, {
        id: finished.run_id, role: 'assistant', text: answer.text, citations: answer.citations,
      }])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '请求失败，请稍后重试')
    } finally {
      setPending(false)
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); void submit(draft) }
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(draft) }
  }
  function resetConversation() {
    sessionStorage.removeItem(sessionKey)
    conversationId()
    setMessages([])
    setError(null)
  }

  return <main className="app-shell">
    <header className="topbar">
      <a className="identity" href="#chat" aria-label="数字分身首页"><span className="monogram">{profile.monogram}</span><span>{profile.name} / Digital Twin</span></a>
      <div className="topbar-actions">
        {profile.github && <a className="icon-link" href={profile.github} target="_blank" rel="noreferrer" title="GitHub"><Github size={18} /></a>}
        <button className="icon-button" type="button" onClick={resetConversation} title="开始新的临时对话" disabled={pending}><RotateCcw size={18} /></button>
      </div>
    </header>
    <section className="chat-layout" id="chat">
      <aside className="profile-panel">
        <div className="portrait" aria-hidden="true"><Bot size={52} strokeWidth={1.25} /><span className="signal signal-one" /><span className="signal signal-two" /></div>
        <p className="eyebrow">PERSONAL AGENT</p>
        <h1>{profile.greeting || `你好，我是 ${profile.name} 的 AI 数字分身。`}</h1>
        <p>{profile.role ? `我基于授权项目资料，用第一人称介绍${profile.role}与相关项目设计。` : '我基于授权项目资料，用第一人称介绍项目与工程实践。'}</p>
        {profile.covered_topics.length > 0 && <div className="covered" aria-label="知识覆盖主题">{profile.covered_topics.map((topic) => <span key={topic}>{topic}</span>)}</div>}
        <p className="disclosure"><Sparkles size={16} />这是 AI，不是本人实时在线。资料未覆盖时，我会明确说明。</p>
        <div className="topics" aria-label="推荐问题">{suggested.map((item) => <button key={item.question} type="button" onClick={() => void submit(item.question)} disabled={pending}>{item.question}</button>)}</div>
      </aside>
      <section className="conversation" aria-label="对话">
        {messages.length === 0 && <div className="empty-state"><span className="empty-mark">{profile.monogram}</span><h2>从一个项目问题开始</h2><p>可以问框架设计、系统边界，或我的工程取舍。</p></div>}
        <div className="messages">
          {messages.map((message) => <article className={`message ${message.role}`} key={message.id}>
            <div className="message-label">{message.role === 'user' ? '你' : `${profile.name} 的 AI 分身`}</div>
            <p>{message.text}</p>
            {message.citations && message.citations.length > 0 && <div className="citations">{message.citations.map((citation) => <div className="citation" key={citation.source_id}>
              <span>{citation.project}</span><strong>{citation.title}</strong><p>{citation.summary}</p>
              {citation.url && <a href={citation.url} target="_blank" rel="noreferrer">查看公开资料 <ExternalLink size={14} /></a>}
            </div>)}</div>}
          </article>)}
          {pending && <article className="message assistant pending"><div className="message-label">{profile.name} 的 AI 分身</div><p><LoaderCircle size={16} /> {statusText('running')}</p></article>}
          {error && <div className="error-notice" role="alert">{error}</div>}
          <div ref={bottomRef} />
        </div>
        <form className="composer" onSubmit={onSubmit}>
          <label className="sr-only" htmlFor="question">提问</label>
          <textarea id="question" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} placeholder="问我一个项目或工程问题..." maxLength={2000} rows={1} disabled={pending} />
          <button type="submit" aria-label="发送问题" title="发送" disabled={pending || !draft.trim()}><ArrowUp size={20} /></button>
        </form>
        <p className="retention">此标签页的临时对话会保留至多 24 小时。</p>
      </section>
    </section>
  </main>
}

createRoot(document.getElementById('root')!).render(<App />)
