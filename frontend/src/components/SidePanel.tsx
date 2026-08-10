import { useState } from 'react'
import { useAuthStore } from '../stores/authStore'

interface NodeDetail {
  hash: string
  field_type: string
  expression: any
  upstream: { hash: string; label: string; field_type: string }[]
  downstream: { hash: string; label: string; field_type: string }[]
}

const TYPE_COLORS: Record<string, string> = {
  atomic: '#22c55e', literal: '#94a3b8', expression: '#3b82f6',
  atomic_condition: '#eab308', composite_condition: '#f97316',
  table: '#3b82f6',
}

export default function SidePanel({
  detail,
  queryId,
  onClose,
}: {
  detail: NodeDetail | null
  queryId?: number
  onClose: () => void
}) {
  const token = useAuthStore((s) => s.token)
  const [sqlModal, setSqlModal] = useState(false)
  const [sqlResult, setSqlResult] = useState('')
  const [sqlLoading, setSqlLoading] = useState(false)

  if (!detail) return null

  const handleRestoreSQL = async () => {
    setSqlLoading(true)
    try {
      const qs = queryId ? `?query_id=${queryId}` : ''
      const res = await fetch(`/api/v1/lineage/field-sql/${detail.hash}${qs}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const json = await res.json()
      if (json.code === 0) {
        setSqlResult(json.data.sql)
      } else {
        setSqlResult(`还原失败: ${json.message}`)
      }
    } catch (e: any) {
      setSqlResult(`请求失败: ${e.message}`)
    }
    setSqlLoading(false)
    setSqlModal(true)
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(sqlResult)
  }

  return (
    <>
      <div className="w-80 bg-white border-l overflow-y-auto shrink-0 text-xs flex flex-col">
        <div className="flex items-center justify-between px-3 py-2 border-b sticky top-0 bg-white">
          <span className="font-medium text-gray-700">节点详情</span>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>

        <div className="p-3 space-y-3 flex-1">
          {/* 基本信息 */}
          <div>
            <div className="text-gray-400 mb-1">Hash</div>
            <div className="font-mono text-gray-700 break-all">{detail.hash}</div>
          </div>
          <div>
            <div className="text-gray-400 mb-1">类型</div>
            <span
              className="inline-block px-2 py-0.5 rounded text-white"
              style={{ backgroundColor: TYPE_COLORS[detail.field_type] || '#6b7280' }}
            >
              {detail.field_type}
            </span>
          </div>
          {/* 表节点：展示列结构 */}
          {detail.field_type === 'table' && detail.expression.columns && (
            <div>
              <div className="text-gray-400 mb-1">
                列结构 ({detail.expression.columns.length})
              </div>
              <div className="space-y-0.5 max-h-60 overflow-auto">
                {detail.expression.columns.map((col: any) => (
                  <div
                    key={col.name}
                    className="flex items-center gap-2 px-2 py-1 bg-gray-50 rounded"
                  >
                    <span className="font-mono text-gray-700">{col.name}</span>
                    <span className="text-gray-400 ml-auto">{col.type}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 非表节点：显示 JSON 表达式 */}
          {detail.field_type !== 'table' && (
            <div>
              <div className="text-gray-400 mb-1">表达式</div>
              <pre className="bg-gray-50 rounded p-2 font-mono text-gray-700 overflow-auto max-h-40">
                {JSON.stringify(detail.expression, null, 2)}
              </pre>
            </div>
          )}

          {/* 上游 */}
          {detail.upstream.length > 0 && (
            <div>
              <div className="text-gray-400 mb-1">上游 ({detail.upstream.length})</div>
              <div className="space-y-1">
                {detail.upstream.map((u) => (
                  <div
                    key={u.hash}
                    className="flex items-center gap-2 px-2 py-1 bg-gray-50 rounded"
                  >
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: TYPE_COLORS[u.field_type] || '#6b7280' }}
                    />
                    <span className="font-mono text-gray-500">{u.hash.slice(0, 8)}</span>
                    <span className="text-gray-700 truncate">{u.label}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 下游 */}
          {detail.downstream.length > 0 && (
            <div>
              <div className="text-gray-400 mb-1">下游 ({detail.downstream.length})</div>
              <div className="space-y-1">
                {detail.downstream.map((d) => (
                  <div
                    key={d.hash}
                    className="flex items-center gap-2 px-2 py-1 bg-gray-50 rounded"
                  >
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: TYPE_COLORS[d.field_type] || '#6b7280' }}
                    />
                    <span className="font-mono text-gray-500">{d.hash.slice(0, 8)}</span>
                    <span className="text-gray-700 truncate">{d.label}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 还原SQL 按钮 — 仅字段节点 */}
        {detail.field_type !== 'table' && (
          <div className="px-3 pb-3">
            <button
              onClick={handleRestoreSQL}
              disabled={sqlLoading}
              className="w-full text-xs px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-50 text-gray-600"
            >
              {sqlLoading ? '还原中...' : '🔄 还原SQL'}
            </button>
          </div>
        )}
      </div>

      {/* SQL 还原结果弹窗 */}
      {sqlModal && (
        <div
          className="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
          onClick={() => setSqlModal(false)}
        >
          <div
            className="bg-white rounded-lg p-4 w-[500px] max-h-[80vh] overflow-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-medium text-sm">字段 SQL 还原</h3>
              <button onClick={() => setSqlModal(false)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            <div className="text-xs text-gray-400 mb-2 font-mono break-all">
              Hash: {detail.hash}
            </div>
            <pre className="bg-gray-50 rounded p-3 text-xs font-mono whitespace-pre-wrap overflow-auto max-h-60">
              {sqlResult}
            </pre>
            <div className="flex justify-end gap-2 mt-3">
              <button
                onClick={handleCopy}
                className="text-xs px-3 py-1 rounded border hover:bg-gray-50"
              >
                复制
              </button>
              <button
                onClick={() => setSqlModal(false)}
                className="text-xs px-3 py-1 rounded bg-blue-600 text-white hover:bg-blue-700"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
