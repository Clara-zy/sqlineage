import { useState, useCallback, useEffect, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Handle,
  Position,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import ELK from 'elkjs/lib/elk.bundled.js'
import { useAuthStore } from '../stores/authStore'
import SidePanel from '../components/SidePanel'

/* ── 颜色 / 节点 ── */
const TYPE_COLORS: Record<string, string> = {
  atomic: '#22c55e',
  literal: '#94a3b8',
  expression: '#3b82f6',
  atomic_condition: '#eab308',
  composite_condition: '#f97316',
}

function FieldNode({ data }: NodeProps) {
  const d = data as any
  const color = TYPE_COLORS[d.field_type] || '#6b7280'
  return (
    <div
      className="rounded-lg border-2 px-3 py-2 text-xs shadow-sm bg-white min-w-[130px]"
      style={{ borderColor: color }}
    >
      <Handle type="target" position={Position.Top} />
      <div className="font-mono text-gray-400">{d.hash}</div>
      <div className="font-medium text-gray-800 mt-0.5">{d.label}</div>
      <div className="text-gray-400 mt-0.5">{d.field_type}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}

function TableNode({ data }: NodeProps) {
  const d = data as any
  return (
    <div
      className="rounded-lg border-2 px-3 py-2 text-xs shadow-sm bg-blue-50 min-w-[130px]"
      style={{ borderColor: '#3b82f6' }}
    >
      <Handle type="target" position={Position.Top} />
      <div className="text-gray-400 text-xs">{d.db_name || '—'}</div>
      <div className="font-medium text-gray-800">{d.table_name}</div>
      <div className="text-gray-400 mt-0.5">表</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}

const nodeTypes = { fieldNode: FieldNode, tableNode: TableNode }

/* ── elkjs 布局 ── */
const elk = new ELK()

async function autoLayout(
  rawNodes: { id: string; type: string; data: any }[],
  rawEdges: { id: string; source: string; target: string }[]
) {
  const graph = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'DOWN',
      'elk.spacing.nodeNode': '30',
      'elk.layered.spacing.nodeNodeBetweenLayers': '60',
    },
    children: rawNodes.map((n) => ({ id: n.id, width: 140, height: 70 })),
    edges: rawEdges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  }
  const layout = await elk.layout(graph)
  if (!layout.children) return rawNodes
  const posMap = new Map<string, { x: number; y: number }>()
  for (const child of layout.children) posMap.set(child.id, { x: child.x ?? 0, y: child.y ?? 0 })
  return rawNodes.map((n) => {
    const pos = posMap.get(n.id)
    return pos ? { ...n, position: pos } : n
  })
}

/* ── 自动聚焦 — 必须在 ReactFlow 树内才能用 useReactFlow ── */
function FitViewOnChange({ nodes }: { nodes: any[] }) {
  const { fitView } = useReactFlow()
  useEffect(() => {
    if (nodes.length > 0) {
      const t = setTimeout(() => fitView({ padding: 0.2, duration: 300 }), 50)
      return () => clearTimeout(t)
    }
  }, [nodes, fitView])
  return null
}

/* ── 主页面 ── */
export default function TestCasePage() {
  const token = useAuthStore((s) => s.token)
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([])
  const setSelectedHash = useState<string | null>(null)[1]
  const [nodeDetail, setNodeDetail] = useState<any>(null)
  const [history, setHistory] = useState<any[]>([])
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [keyword, setKeyword] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [detailQuery, setDetailQuery] = useState<any>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [viewMode, setViewMode] = useState<'field' | 'table'>('field')

  const apiHeaders = useMemo(
    () => ({ 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }),
    [token]
  )

  /* ── 加载图谱 ── */
  const loadGraph = useCallback(async (ids?: number[]) => {
    if (!ids || ids.length === 0) {
      setNodes([])
      setEdges([])
      return
    }
    const endpoint = viewMode === 'table' ? '/api/v1/lineage/overview' : '/api/v1/lineage/graph'
    const url = endpoint + '?query_ids=' + ids.join(',')
    const res = await fetch(url, { headers: apiHeaders })
    const json = await res.json()
    if (json.code === 0 && json.data.nodes.length > 0) {
      const laidOut = await autoLayout(json.data.nodes, json.data.edges)
      // 表级视图：给边加标签样式
      const styledEdges = json.data.edges.map((e: any) => ({
        ...e,
        labelStyle: { fill: '#6b7280', fontSize: 10, fontWeight: 500 },
        labelBgStyle: { fill: '#f9fafb', fillOpacity: 0.9 },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 2,
      }))
      setNodes(laidOut)
      setEdges(styledEdges)
    } else {
      setNodes([])
      setEdges([])
    }
  }, [apiHeaders, setNodes, setEdges, viewMode])

  /* ── 加载历史 ── */
  useEffect(() => {
    fetch('/api/v1/lineage/history', { headers: apiHeaders })
      .then((r) => r.json())
      .then((json) => { if (json.code === 0) setHistory(json.data.items) })
  }, [apiHeaders])

  /* ── 过滤 ── */
  const filtered = useMemo(() => {
    let items = history
    if (keyword.trim()) {
      const kw = keyword.toLowerCase()
      items = items.filter(
        (h: any) =>
          (h.comment || '').toLowerCase().includes(kw) ||
          (h.sql_text || '').toLowerCase().includes(kw)
      )
    }
    return items
  }, [history, keyword])

  /* ── 勾选切换 ── */
  const toggleQuery = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      setTimeout(() => {
        const arr = Array.from(next)
        loadGraph(arr.length > 0 ? arr : undefined)
      }, 50)
      return next
    })
  }, [loadGraph])

  /* ── 全选/全不选 ── */
  const selectAll = useCallback(() => {
    const allIds = filtered.map((h: any) => h.id)
    setSelectedIds(new Set(allIds))
    setTimeout(() => loadGraph(allIds), 50)
  }, [filtered, loadGraph])

  const deselectAll = useCallback(() => {
    setSelectedIds(new Set())
    setNodes([])
    setEdges([])
  }, [setNodes, setEdges])

  /* ── 节点点击 ── */
  const onNodeClick = useCallback(async (_: any, node: any) => {
    setSelectedHash(node.id)
    if (node.type === 'tableNode') {
      setNodeDetail({
        hash: node.id,
        field_type: 'table',
        expression: node.data,
        upstream: [],
        downstream: [],
      })
    } else {
      const res = await fetch(`/api/v1/lineage/node/${node.id}`, { headers: apiHeaders })
      const json = await res.json()
      if (json.code === 0) setNodeDetail(json.data)
    }
  }, [apiHeaders])

  /* ── 提取所有标签 ── */
  const allTags = useMemo(() => {
    const tags = new Set<string>()
    for (const h of history) {
      const c = h.comment || ''
      // 旧格式: "[L0-01] 标题"
      const m1 = c.match(/^\[(\w+)-\d+\]/)
      if (m1) { tags.add(m1[1]); continue }
      // BIRD 格式: "[BIRD] financial/simple Q89: ..."
      const m2 = c.match(/^\[BIRD\]\s+(\w+)\/(\w+)/)
      if (m2) { tags.add(m2[1]); tags.add(m2[2]) }
    }
    return Array.from(tags).sort()
  }, [history])

  return (
    <div className="h-[calc(100vh-52px)] w-full flex flex-col">
      {/* 顶栏 */}
      <div className="bg-white border-b px-4 py-2 flex items-center gap-3 shrink-0">
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className={`text-xs px-2 py-1 rounded border ${sidebarOpen ? 'bg-gray-100' : ''}`}
          title={sidebarOpen ? '收起清单' : '展开清单'}
        >
          {sidebarOpen ? '◀ 收起' : '▶ 展开'}
        </button>
        <span className="text-sm font-bold text-green-700">测例浏览</span>
        <div className="flex items-center gap-0.5 ml-2">
          <button
            onClick={() => setViewMode('field')}
            className={`text-xs px-2 py-1 rounded-l ${viewMode === 'field' ? 'bg-green-100 text-green-700 font-medium' : 'bg-gray-100 text-gray-500'}`}
          >
            字段级
          </button>
          <button
            onClick={() => setViewMode('table')}
            className={`text-xs px-2 py-1 rounded-r ${viewMode === 'table' ? 'bg-purple-100 text-purple-700 font-medium' : 'bg-gray-100 text-gray-500'}`}
          >
            表级
          </button>
        </div>
        <span className="text-xs text-gray-400">
          {selectedIds.size > 0
            ? `已选 ${selectedIds.size} / ${filtered.length} 项`
            : `${filtered.length} 条测例`}
        </span>
        <div className="flex-1" />
        <button
          onClick={selectAll}
          className="text-xs px-3 py-1 rounded border hover:bg-gray-50"
        >
          全选
        </button>
        <button
          onClick={deselectAll}
          className="text-xs px-3 py-1 rounded border hover:bg-gray-50"
        >
          取消全选
        </button>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* 左侧清单（可折叠） */}
        {sidebarOpen && (
        <div className="w-64 bg-white border-r overflow-auto shrink-0">
          {/* 搜索 */}
          <div className="p-2 border-b space-y-1 sticky top-0 bg-white z-10">
            <input
              className="w-full border rounded px-2 py-1 text-xs"
              placeholder="搜索测例标题/SQL..."
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <div className="flex gap-1 flex-wrap">
              <button
                onClick={() => setTagFilter('')}
                className={`text-xs px-2 py-0.5 rounded ${!tagFilter ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'}`}
              >
                全部
              </button>
              {allTags.map((t) => (
                <button
                  key={t}
                  onClick={() => setTagFilter(t === tagFilter ? '' : t)}
                  className={`text-xs px-2 py-0.5 rounded ${t === tagFilter ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'}`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* 列表 */}
          <div className="p-1 space-y-0.5">
            {filtered
              .filter((h: any) => {
                if (!tagFilter) return true
                const c = h.comment || ''
                // 旧格式 [L0-01] 或 BIRD 格式 [BIRD] financial/simple
                return c.includes(`[${tagFilter}-`) || c.includes(`/${tagFilter} `)
              })
              .map((h: any) => (
                <label
                  key={h.id}
                  className="flex items-start gap-2 text-xs p-1.5 rounded hover:bg-gray-50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(h.id)}
                    onChange={() => toggleQuery(h.id)}
                    className="mt-0.5 shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-gray-700 truncate">
                      {(h.comment || h.sql_text || '').replace(/^\[.*?\]\s*/, '')}
                    </div>
                    <div className="text-gray-400 text-xs mt-0.5 flex items-center gap-1">
                      {h.comment?.includes('[BIRD]') && (
                        <>
                          <span className="bg-purple-50 text-purple-600 px-1 rounded text-xs">{h.comment.match(/\[BIRD\]\s+(\w+)\//)?.[1]}</span>
                          <span className="bg-yellow-50 text-yellow-600 px-1 rounded text-xs">{h.comment.match(/\/(\w+)\s/)?.[1]}</span>
                        </>
                      )}
                      +{h.field_count}字段 +{h.sq_count}SQ
                    </div>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); setDetailQuery(h) }}
                    className="bg-gray-50 hover:bg-blue-50 text-gray-400 hover:text-blue-600 shrink-0 text-xs px-1.5 py-0.5 rounded border"
                    title="查看完整 SQL"
                  >
                    📋
                  </button>
                </label>
              ))}
            {filtered.length === 0 && (
              <div className="text-xs text-gray-400 p-3 text-center">
                暂无测例，请先运行 <code className="bg-gray-100 px-1 rounded">make import-tests</code>
              </div>
            )}
          </div>
        </div>
        )}

        {/* 右侧 DAG 画布 */}
        <div className="flex-1 flex">
          <div className="flex-1">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={onNodeClick}
              nodeTypes={nodeTypes}
            >
              <Background />
              <Controls />
              <MiniMap nodeColor={(n: any) => {
                if (n.type === 'tableNode') return '#3b82f6'
                return TYPE_COLORS[n.data?.field_type] || '#6b7280'
              }} />
              <FitViewOnChange nodes={nodes} />
            </ReactFlow>
          </div>
          <SidePanel
            detail={nodeDetail}
            onClose={() => { setSelectedHash(null); setNodeDetail(null) }}
          />
        </div>
      </div>

      {/* SQL 详情弹窗 */}
      {detailQuery && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setDetailQuery(null)}>
          <div className="bg-white rounded-lg p-4 w-[600px] max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-medium text-sm">{detailQuery.comment || '查询详情'}</h3>
              <button onClick={() => setDetailQuery(null)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            <div className="text-xs text-gray-500 mb-2">
              ID: {detailQuery.id} &nbsp; +{detailQuery.field_count}字段 +{detailQuery.sq_count}SQ &nbsp; {detailQuery.created_at?.slice(0, 16)}
            </div>
            <pre className="bg-gray-50 rounded p-3 text-xs font-mono whitespace-pre-wrap overflow-auto max-h-96">
              {detailQuery.sql_text}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
