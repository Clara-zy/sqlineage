interface Props {
  page: number
  pageSize: number
  total: number
  onChange: (page: number) => void
  onSizeChange: (size: number) => void
}

const SIZES = [10, 20, 30, 50, 100]

export default function Pagination({ page, pageSize, total, onChange, onSizeChange }: Props) {
  const totalPages = Math.ceil(total / pageSize)

  return (
    <div className="flex items-center gap-2 mt-3 text-xs">
      <span className="text-gray-400">每页</span>
      <select
        value={pageSize}
        onChange={(e) => onSizeChange(Number(e.target.value))}
        className="border rounded px-1.5 py-0.5"
      >
        {SIZES.map((s) => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>
      <span className="text-gray-400">条</span>

      {totalPages > 1 && (
        <>
          <button
            onClick={() => onChange(page - 1)}
            disabled={page <= 1}
            className="px-2 py-1 border rounded disabled:opacity-30 hover:bg-gray-50"
          >
            ←
          </button>
          <span className="text-gray-500 px-1">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => onChange(page + 1)}
            disabled={page >= totalPages}
            className="px-2 py-1 border rounded disabled:opacity-30 hover:bg-gray-50"
          >
            →
          </button>
        </>
      )}
      <span className="text-gray-400">共 {total} 条</span>
    </div>
  )
}
