import { useState, useCallback, useEffect, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import ELK from 'elkjs/lib/elk.bundled.js'
import { useAuthStore } from '../stores/authStore'
import SidePanel from '../components/SidePanel'

/* ── 字段节点颜色 ── */
const TYPE_COLORS: Record<string, string> = {
  atomic: '#22c55e',
  literal: '#94a3b8',
  expression: '#3b82f6',
  atomic_condition: '#eab308',
  composite_condition: '#f97316',
}

/* ── 自定义字段节点 ── */
function FieldNode({ data }: NodeProps) {
  const color = TYPE_COLORS[data.field_type] || '#6b7280'
  return (
    <div
      className="rounded-lg border-2 px-3 py-2 text-xs shadow-sm bg-white min-w-[130px]"
      style={{ borderColor: color }}
    >
      <Handle type="target" position={Position.Top} />
      <div className="font-mono text-gray-400">{data.hash}</div>
      <div className="font-medium text-gray-800 mt-0.5">{data.label}</div>
      <div className="text-gray-400 mt-0.5">{data.field_type}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}

const nodeTypes = { fieldNode: FieldNode }

/* ── elkjs 自上而下分层布局 ── */
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
    children: rawNodes.map((n) => ({
      id: n.id,
      width: 140,
      height: 70,
    })),
    edges: rawEdges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  }

  const layout = await elk.layout(graph)
  if (!layout.children) return rawNodes

  const posMap = new Map<string, { x: number; y: number }>()
  for (const child of layout.children) {
    posMap.set(child.id, { x: child.x ?? 0, y: child.y ?? 0 })
  }

  return rawNodes.map((n) => {
    const pos = posMap.get(n.id)
    return pos ? { ...n, position: pos } : n
  })
}

/* ── 主页面 ── */
export default function WorkbenchPage() {
  const token = useAuthStore((s) => s.token)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [sql, setSql] = useState('')
  const [sinkTable, setSinkTable] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [selectedHash, setSelectedHash] = useState<string | null>(null)
  const [nodeDetail, setNodeDetail] = useState<any>(null)

  const apiHeaders = useMemo(
    () => ({ 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }),
    [token]
  )

  /* ── 加载图谱 + elkjs 布局 ── */
  const loadGraph = useCallback(async () => {
    const res = await fetch('/api/v1/lineage/graph', { headers: apiHeaders })
    const json = await res.json()
    if (json.code === 0 && json.data.nodes.length > 0) {
      const laidOut = await autoLayout(json.data.nodes, json.data.edges)
      setNodes(laidOut)
      setEdges(json.data.edges)
    } else {
      setNodes([])
      setEdges([])
    }
  }, [apiHeaders, setNodes, setEdges])

  useEffect(() => { loadGraph() }, [])

  /* ── 构建血缘 ── */
  const handleBuild = useCallback(async () => {
    if (!sql.trim()) return
    setStatus('building')
    setError('')
    try {
      const res = await fetch('/api/v1/lineage/build', {
        method: 'POST',
        headers: apiHeaders,
        body: JSON.stringify({ sql, sink_table: sinkTable || null }),
      })
      const json = await res.json()
      if (json.code === 0) {
        setStatus(`构建完成: +${json.data.new_fields} 字段, +${json.data.new_subqueries} 子查询`)
        await loadGraph()
      } else {
        setError(json.message)
        setStatus('error')
      }
    } catch (e: any) {
      setError(e.message)
      setStatus('error')
    }
  }, [sql, apiHeaders, loadGraph])

  /* ── 节点点击 → 获取详情 ── */
  const onNodeClick = useCallback(
    async (_: any, node: any) => {
      setSelectedHash(node.id)
      const res = await fetch(`/api/v1/lineage/node/${node.id}`, { headers: apiHeaders })
      const json = await res.json()
      if (json.code === 0) setNodeDetail(json.data)
    },
    [apiHeaders]
  )

  /* ── 清空 ── */
  const handleClear = useCallback(async () => {
    await fetch('/api/v1/lineage/clear', { method: 'POST', headers: apiHeaders })
    setNodes([])
    setEdges([])
    setStatus('')
    setError('')
  }, [apiHeaders, setNodes, setEdges])

  return (
    <div className="h-[calc(100vh-52px)] w-full flex flex-col">
      {/* 工具栏 */}
      <div className="bg-white border-b px-4 py-2 flex items-center gap-3 shrink-0">
        <span className="text-sm font-bold text-blue-700">SQLineage</span>
        <textarea
          className="flex-1 border rounded px-2 py-1 text-xs font-mono resize-none h-10"
          placeholder="输入 SQL，如: SELECT a.trans_id, b.mchnt_nm FROM trans_flow a LEFT JOIN dim_mchnt b ON a.mchnt_cd=b.mchnt_cd"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          rows={2}
        />
        <input
          className="w-40 border rounded px-2 py-1 text-xs"
          placeholder="sink表(可选)"
          value={sinkTable}
          onChange={(e) => setSinkTable(e.target.value)}
        />
        <button
          onClick={handleBuild}
          disabled={status === 'building'}
          className="bg-blue-600 text-white text-xs px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 whitespace-nowrap"
        >
          {status === 'building' ? '构建中...' : '构建血缘'}
        </button>
        <button
          onClick={loadGraph}
          className="text-xs px-3 py-1.5 rounded border hover:bg-gray-50 whitespace-nowrap"
        >
          刷新
        </button>
        <button
          onClick={handleClear}
          className="text-xs px-3 py-1.5 rounded border hover:bg-red-50 text-red-600 whitespace-nowrap"
        >
          清空
        </button>
      </div>

      {/* 状态栏 */}
      {status && (
        <div className={`text-xs px-4 py-1 shrink-0 ${error ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'}`}>
          {status}
        </div>
      )}

      {/* 画布 + 侧栏 */}
      <div className="flex-1 flex">
        <div className="flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            nodeTypes={nodeTypes}
            fitView
          >
          <Background />
          <Controls />
          <MiniMap
            nodeColor={(n) => TYPE_COLORS[n.data?.field_type] || '#6b7280'}
          />
        </ReactFlow>
        </div>
        <SidePanel detail={nodeDetail} onClose={() => { setSelectedHash(null); setNodeDetail(null) }} />
      </div>
    </div>
  )
}
