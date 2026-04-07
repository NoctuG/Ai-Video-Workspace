import React, { useMemo, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || window.location.origin

const defaultSettings = {
  api_providers: [
    { name: 'memefast', base_url: 'https://api.memefast.example', keys: ['k1', 'k2'] },
    { name: 'runninghub', base_url: 'https://api.runninghub.example', keys: ['r1', 'r2'] },
  ],
  routes: [
    { capability: 'image_generation', provider_name: 'memefast', model: 'gemini-3-pro-image-preview' },
    { capability: 'video_generation', provider_name: 'runninghub', model: 'doubao-seedance-1-5-pro-251215' },
    { capability: 'llm', provider_name: 'memefast', model: 'gpt-4.1' },
  ],
  image_bed: { provider: 'oss-proxy', endpoint: 'https://imgbed.example/upload', keys: ['i1', 'i2'] },
}

export default function App() {
  const [token, setToken] = useState(localStorage.getItem('avw_token') || '')
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('admin123')
  const [projectId, setProjectId] = useState('')
  const [script, setScript] = useState('场景1: 雨夜街道\n主角: 我要救她。\n场景2: 天台\n反派: 你来晚了。')
  const [project, setProject] = useState(null)
  const [calibration, setCalibration] = useState(null)
  const [settingsJson, setSettingsJson] = useState(JSON.stringify(defaultSettings, null, 2))
  const [cameraMotion, setCameraMotion] = useState('推镜')
  const [lighting, setLighting] = useState('电影感光影')
  const [effects, setEffects] = useState('胶片颗粒,体积光')
  const [tasks, setTasks] = useState({})
  const [logs, setLogs] = useState([])

  const sortedTasks = useMemo(
    () => Object.values(tasks).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0)),
    [tasks]
  )

  const authHeaders = token ? { Authorization: `Bearer ${token}` } : {}

  const log = (message) => setLogs((x) => [`${new Date().toLocaleTimeString()} ${message}`, ...x].slice(0, 30))

  async function api(path, opts = {}) {
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        ...(opts.headers || {}),
      },
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  }

  async function login() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setToken(data.token)
    localStorage.setItem('avw_token', data.token)
    log('登录成功')
    connectWs(data.token)
  }

  async function saveSettings() {
    const payload = JSON.parse(settingsJson)
    await api('/api/settings', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify(payload),
    })
    log('系统配置已保存')
  }

  async function importScript() {
    const data = await api('/api/script/import', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify({ project_name: 'AI Video Project', raw_script: script }),
    })
    setProject(data)
    setProjectId(data.id)
    log(`剧本解析完成，项目ID: ${data.id}`)
  }

  async function runCalibration() {
    const data = await api('/api/calibration', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify({ project_id: projectId, style: 'cinematic' }),
    })
    setCalibration(data)
    log('角色+场景+分镜提示词校准完成')
  }

  async function generateImages() {
    await api('/api/workflow/generate-images', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify({ project_id: projectId }),
    })
    log('已提交角色/场景图生成任务')
  }

  async function generateVideos() {
    await api('/api/workflow/generate-videos', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify({
        project_id: projectId,
        camera_motion: cameraMotion,
        lighting,
        effects: effects.split(',').map((x) => x.trim()).filter(Boolean),
      }),
    })
    log('已提交图生视频任务（含运镜/光影/特效参数）')
  }

  async function stitchExport() {
    const segmentIds = sortedTasks.filter((t) => t.task_type === 'video_generation' && t.status === 'completed').map((t) => t.id)
    const data = await api('/api/workflow/stitch-export', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify({ project_id: projectId, segment_task_ids: segmentIds }),
    })
    log(`已提交逐段拼接导出任务: ${data.task_id}`)
  }

  function connectWs(useToken = token) {
    if (!useToken) return
    const ws = new WebSocket(`${API_BASE.replace(/^http/, 'ws')}/ws/tasks?token=${useToken}`)
    ws.onopen = () => ws.send('subscribe')
    ws.onmessage = (evt) => {
      const payload = JSON.parse(evt.data)
      if (payload.task) {
        setTasks((old) => ({ ...old, [payload.task.id]: payload.task }))
      }
    }
    ws.onclose = () => setTimeout(() => connectWs(useToken), 1500)
  }

  return (
    <div className="app">
      <h1>AI Video Workspace Dashboard</h1>

      <section className="panel">
        <h2>登录（部署时由后端环境变量设置凭据）</h2>
        <div className="row">
          <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="username" />
          <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder="password" />
          <button onClick={login}>登录</button>
        </div>
      </section>

      <section className="panel">
        <h2>1) 系统配置</h2>
        <textarea value={settingsJson} onChange={(e) => setSettingsJson(e.target.value)} rows={8} />
        <button onClick={saveSettings} disabled={!token}>保存配置</button>
      </section>

      <section className="panel">
        <h2>2) 剧本 → 3) 角色+场景校准</h2>
        <textarea value={script} onChange={(e) => setScript(e.target.value)} rows={6} />
        <div className="row">
          <button onClick={importScript} disabled={!token}>导入并拆解剧本</button>
          <button onClick={runCalibration} disabled={!projectId || !token}>执行校准</button>
        </div>
        <p>projectId: {projectId || '-'}</p>
      </section>

      <section className="panel">
        <h2>4) 图像生成 → 5) 图生视频 → 6) 逐段拼接导出</h2>
        <div className="row">
          <button onClick={generateImages} disabled={!projectId || !token}>生成角色+场景图</button>
          <input value={cameraMotion} onChange={(e) => setCameraMotion(e.target.value)} placeholder="运镜" />
          <input value={lighting} onChange={(e) => setLighting(e.target.value)} placeholder="光影" />
          <input value={effects} onChange={(e) => setEffects(e.target.value)} placeholder="画面特效(逗号分隔)" />
          <button onClick={generateVideos} disabled={!projectId || !token}>图再生视频</button>
          <button onClick={stitchExport} disabled={!projectId || !token}>逐段拼接导出成片</button>
        </div>
      </section>

      <section className="panel">
        <h2>任务看板</h2>
        <ul>
          {sortedTasks.map((t) => (
            <li key={t.id}>
              [{t.task_type}] {t.status} {t.progress}% {t.result?.asset_url || ''}
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <h2>结构化数据预览</h2>
        <pre>{JSON.stringify({ project, calibration }, null, 2)}</pre>
      </section>

      <section className="panel">
        <h2>日志</h2>
        <pre>{logs.join('\n')}</pre>
      </section>
    </div>
  )
}
