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
}

function exprLabel(field_type: string, expr: any): string {
  const op = expr?.op || '?'
  if (op === 'column') return `${expr.table}.${expr.column}`
  if (op === 'alias') return `AS ${expr.alias_name || '?'}`
  if (op === 'literal') return `'${String(expr.value || '').slice(0, 12)}'`
  if (op === 'star') return '*'
  if (op === 'JOIN') return 'JOIN(...)'
  if (op === 'casewhen') return 'CASE WHEN'
  return op
}

export default function SidePanel({
  detail,
  onClose,
}: {
  detail: NodeDetail | null
  onClose: () => void
}) {
  if (!detail) return null

  return (
    <div className="w-80 bg-white border-l overflow-y-auto shrink-0 text-xs">
      <div className="flex items-center justify-between px-3 py-2 border-b sticky top-0 bg-white">
        <span className="font-medium text-gray-700">节点详情</span>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
      </div>

      <div className="p-3 space-y-3">
        {/* 基本信息 */}
        <div>
          <div className="text-gray-400 mb-1">Hash</div>
          <div className="font-mono text-gray-700">{detail.hash}</div>
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
        <div>
          <div className="text-gray-400 mb-1">表达式</div>
          <pre className="bg-gray-50 rounded p-2 font-mono text-gray-700 overflow-auto max-h-40">
            {JSON.stringify(detail.expression, null, 2)}
          </pre>
        </div>

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
    </div>
  )
}
