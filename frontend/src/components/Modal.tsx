import { useEffect } from 'react'
import { X } from 'lucide-react'

interface Props {
  title: string
  children: React.ReactNode
  onClose: () => void
  footer?: React.ReactNode
  maxWidth?: string
}

export default function Modal({ title, children, onClose, footer, maxWidth = 'max-w-md' }: Props) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Panel */}
      <div className={`relative bg-gray-900 border border-gray-700/80 rounded-2xl w-full ${maxWidth} shadow-2xl animate-slide-up`}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <h3 className="font-semibold text-white text-sm">{title}</h3>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 transition-colors p-0.5 rounded"
          >
            <X size={15} />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer && (
          <div className="px-5 pb-4 flex justify-end gap-2 border-t border-gray-800 pt-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}
