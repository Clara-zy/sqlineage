import { useState, useEffect, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

interface Column {
  id: number
  column_name: string
  column_type: string
  column_comment: string
  ordinal_position: number
  field_hash: string | null
}

interface TableDetail {
  id: number
  table_name: string
  schema_name: string
  table_type: string
  source: string
  logical_name: string | null
  input_subquery_hash: string | null
  created_at: string
  columns: Column[]
}

export default function TableDetailPage() {
  const { id } = useParams()
  const token = useAuthStore((s) => s.token)
  const [table, setTable] = useState<TableDetail | null>(null)
  const [inputHistory, setInputHistory] = useState<any[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [selectedCol, setSelectedCol] = useState<Column | null>(null)
  const [colHistory, setColHistory] = useState<any[]>([])

  const headers = useMemo(() => ({
    Authorization: `Bearer ${token}`,
  }), [token])

  useEffect(() => {
    fetch(`/api/v1/tables/${id}`, { headers })
      .then((r) => r.json())
      .then((j) => { if (j.code === 0) setTable(j.data) })
  }, [id])

  const loadInputHistory = async () => {
    const res = await fetch(`/api/v1/tables/${id}/input-history`, { headers })
    const json = await res.json()
    if (json.code === 0) setInputHistory(json.data.items)
    setShowHistory(true)
  }

  const loadColHistory = async (col: Column) => {
    setSelectedCol(col)
    const res = await fetch(`/api/v1/tables/${id}/columns/${col.id}/mapping-history`, { headers })
    const json = await res.json()
    if (json.code === 0) setColHistory(json.data.items)
  }

  if (!table) return <div className="p-6 text-gray-500">加载中...</div>

  return (
    <div className="p-6">
      <Link to="/tables" className="text-xs text-blue-600 hover:underline mb-2 inline-block">← 返回列表</Link>
      <h1 className="text-xl font-bold text-gray-800">{table.table_name}</h1>

      <div className="flex gap-4 mt-2 text-xs text-gray-500">
        <span>类型: {table.table_type}</span>
        <span>来源: <span className={table.source === 'root' ? 'text-green-600' : 'text-yellow-600'}>{table.source}</span></span>
        <span>列数: {table.columns.length}</span>
        {table.input_subquery_hash && (
          <span className="font-mono">输入SQ: {table.input_subquery_hash.slice(0, 8)}</span>
        )}
      </div>

      <div className="flex gap-2 mt-3">
        <button onClick={loadInputHistory} className="text-xs border rounded px-2 py-1 hover:bg-gray-50">
          查看输入历史
        </button>
      </div>

      {/* 输入历史弹窗 */}
      {showHistory && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setShowHistory(false)}>
          <div className="bg-white rounded-lg p-4 w-96 max-h-80 overflow-auto" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-medium text-sm mb-2">输入子查询历史</h3>
            {inputHistory.map((h: any) => (
              <div key={h.id} className="text-xs border-b py-1">
                <span className="font-mono">{h.subquery_hash?.slice(0, 12)}</span>
                <span className={`ml-2 ${h.status === 'active' ? 'text-green-600' : 'text-gray-400'}`}>{h.status}</span>
                {h.deprecated_reason && <div className="text-gray-400">{h.deprecated_reason}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 列映射历史弹窗 */}
      {selectedCol && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setSelectedCol(null)}>
          <div className="bg-white rounded-lg p-4 w-96 max-h-80 overflow-auto" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-medium text-sm mb-2">映射历史: {selectedCol.column_name}</h3>
            {colHistory.map((m: any) => (
              <div key={m.id} className="text-xs border-b py-1">
                <span className="font-mono">{m.field_hash?.slice(0, 12)}</span>
                <span className={`ml-2 ${m.status === 'active' ? 'text-green-600' : 'text-gray-400'}`}>{m.status}</span>
                {m.deprecated_reason && <div className="text-gray-400">{m.deprecated_reason}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 列清单 */}
      <table className="w-full text-sm mt-4">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="py-2 w-10">#</th>
            <th>列名</th>
            <th>类型</th>
            <th>备注</th>
            <th>field_hash</th>
          </tr>
        </thead>
        <tbody>
          {table.columns.map((col) => (
            <tr
              key={col.id}
              className="border-b hover:bg-gray-50 cursor-pointer"
              onClick={() => loadColHistory(col)}
            >
              <td className="py-2 text-gray-400">{col.ordinal_position}</td>
              <td className="font-mono text-xs">{col.column_name}</td>
              <td className="text-gray-600 text-xs">{col.column_type}</td>
              <td className="text-gray-500 text-xs">{col.column_comment}</td>
              <td className="font-mono text-xs text-gray-400">{col.field_hash?.slice(0, 12) || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
