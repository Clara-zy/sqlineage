import { useState, useMemo } from 'react'
import { useAuthStore } from '../stores/authStore'

export default function UpgradePage() {
  const token = useAuthStore((s) => s.token)
  const headers = useMemo(() => ({ 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }), [token])

  const [step, setStep] = useState(1)
  const [tableName, setTableName] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [extendedSql, setExtendedSql] = useState('')
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handlePreview = async () => {
    if (!tableName.trim()) return
    setLoading(true)
    setError('')
    const res = await fetch(`/api/v1/lineage/upgrade/preview/${tableName}`, { headers })
    const json = await res.json()
    if (json.code === 0) {
      setPreview(json.data)
      setStep(2)
    } else {
      setError(json.message)
    }
    setLoading(false)
  }

  const handleExecute = async () => {
    if (!extendedSql.trim()) return
    setLoading(true)
    setError('')
    const res = await fetch('/api/v1/lineage/upgrade/execute', {
      method: 'POST',
      headers,
      body: JSON.stringify({ table_name: tableName, extended_sql: extendedSql }),
    })
    const json = await res.json()
    if (json.code === 0) {
      setResult(json.data)
      setStep(4)
    } else {
      setError(json.message)
    }
    setLoading(false)
  }

  return (
    <div className="p-6 max-w-2xl">
      <h1 className="text-xl font-bold text-gray-800 mb-4">root→middle 升级</h1>

      {/* Step 1: 选择表 */}
      <div className={`p-4 rounded border mb-4 ${step >= 1 ? 'bg-white' : 'bg-gray-50 opacity-50'}`}>
        <div className="text-sm font-medium text-gray-700 mb-2">① 选择 root 表</div>
        <input
          className="border rounded px-3 py-1.5 text-sm w-64"
          placeholder="表名, 如 trans_flow"
          value={tableName}
          onChange={(e) => setTableName(e.target.value)}
          disabled={step > 2}
        />
        <button
          onClick={handlePreview}
          disabled={loading || !tableName.trim()}
          className="ml-3 bg-blue-600 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? '查询中...' : '预览影响'}
        </button>
      </div>

      {/* Step 2: 影响预览 */}
      {preview && (
        <div className={`p-4 rounded border mb-4 ${step >= 2 ? 'bg-white' : 'bg-gray-50 opacity-50'}`}>
          <div className="text-sm font-medium text-gray-700 mb-2">② 影响预览</div>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="bg-gray-50 p-2 rounded">
              <span className="text-gray-400">表字段数</span>
              <div className="font-bold text-lg">{preview.field_count}</div>
            </div>
            <div className="bg-gray-50 p-2 rounded">
              <span className="text-gray-400">下游字段</span>
              <div className="font-bold text-lg">{preview.downstream_fields}</div>
            </div>
            <div className="bg-gray-50 p-2 rounded">
              <span className="text-gray-400">总受影响节点</span>
              <div className="font-bold text-lg">{preview.total_affected_nodes}</div>
            </div>
            <div className="bg-gray-50 p-2 rounded">
              <span className="text-gray-400">受影响查询</span>
              <div className="font-bold text-lg">{preview.affected_queries}</div>
            </div>
          </div>
          {preview.affected_middle_tables?.length > 0 && (
            <div className="mt-3 p-2 bg-yellow-50 rounded text-xs">
              <span className="font-medium text-yellow-700">⚠ 涉及 Middle 表穿透:</span>
              {preview.affected_middle_tables.map((t: any) => (
                <span key={t.table_name} className="ml-2 text-yellow-600">{t.table_name}(×{t.count})</span>
              ))}
            </div>
          )}
          <div className="flex gap-2 mt-3">
            <button onClick={() => setStep(1)} className="text-sm border rounded px-3 py-1 hover:bg-gray-50">
              ← 返回修改
            </button>
            <button
              onClick={() => setStep(3)}
              className="bg-green-600 text-white text-sm px-4 py-1.5 rounded hover:bg-green-700"
            >
              确认，填写上游 SQL
            </button>
          </div>
        </div>
      )}

      {/* Step 3: 填写 SQL */}
      {step >= 3 && (
        <div className={`p-4 rounded border mb-4 ${step >= 3 ? 'bg-white' : 'bg-gray-50 opacity-50'}`}>
          <div className="text-sm font-medium text-gray-700 mb-2">③ 填写构建血缘的 SQL</div>
          <textarea
            className="w-full border rounded px-3 py-2 text-xs font-mono h-32"
            placeholder="SELECT col1, col2, ... FROM ancestor_table ..."
            value={extendedSql}
            onChange={(e) => setExtendedSql(e.target.value)}
            disabled={step > 3}
          />
          <div className="flex gap-2 mt-2">
            <button onClick={() => setStep(2)} className="text-sm border rounded px-3 py-1 hover:bg-gray-50">
              ← 返回预览
            </button>
            <button
              onClick={handleExecute}
              disabled={loading || !extendedSql.trim()}
              className="bg-red-600 text-white text-sm px-4 py-1.5 rounded hover:bg-red-700 disabled:opacity-50"
            >
              {loading ? '执行中...' : '⚠ 确认执行升级'}
            </button>
          </div>
        </div>
      )}

      {/* Step 4: 结果 */}
      {result && (
        <div className="p-4 rounded border bg-green-50 mb-4">
          <div className="text-sm font-medium text-green-700 mb-2">④ 升级完成</div>
          <div className="grid grid-cols-3 gap-3 text-xs">
            <div>
              <span className="text-gray-400">升级表</span>
              <div className="font-bold">{result.upgraded_table}</div>
            </div>
            <div>
              <span className="text-gray-400">级联字段</span>
              <div className="font-bold text-lg">{result.cascaded_fields}</div>
            </div>
            <div>
              <span className="text-gray-400">实际变更</span>
              <div className="font-bold text-lg">{result.changed_count}</div>
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="p-3 rounded bg-red-50 text-red-600 text-sm">{error}</div>
      )}
    </div>
  )
}
