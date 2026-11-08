import { useCallback, useEffect, useRef, useState } from 'react'

/** Only schedules feedback; the supplied Rust command owns the validation rules. */
export function useValidation<T, R>(value: T, validate: (value: T) => Promise<R>, enabled = true) {
  const [state, setState] = useState<{ value: T; result?: R; error?: unknown }>()
  const request = useRef(0)
  const check = useCallback(async () => {
    const current = ++request.current
    try {
      const result = await validate(value)
      if (current === request.current) setState({ value, result })
      return result
    } catch (error) {
      if (current === request.current) setState({ value, error })
      throw error
    }
  }, [value, validate])
  useEffect(() => {
    const timer = enabled ? window.setTimeout(() => void check().catch(() => undefined), 150) : undefined
    return () => { window.clearTimeout(timer); request.current++ }
  }, [check, enabled])
  return { result: state?.value === value ? state.result : undefined, error: state?.value === value ? state.error : undefined, check }
}
