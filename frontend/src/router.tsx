import { Children, createContext, isValidElement, MouseEvent, ReactElement, ReactNode, useContext, useEffect, useMemo, useState } from 'react'

type LocationContextValue = { pathname: string; navigate: (to: string, replace?: boolean) => void }
const LocationContext = createContext<LocationContextValue | null>(null)
const ParamsContext = createContext<Record<string, string>>({})

export function BrowserRouter({ children }: { children: ReactNode }) {
  const [pathname, setPathname] = useState(window.location.pathname)
  useEffect(() => {
    const update = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', update)
    return () => window.removeEventListener('popstate', update)
  }, [])
  const value = useMemo<LocationContextValue>(() => ({
    pathname,
    navigate: (to, replace = false) => {
      if (replace) window.history.replaceState({}, '', to)
      else window.history.pushState({}, '', to)
      setPathname(window.location.pathname)
      window.scrollTo({ top: 0, behavior: 'instant' })
    },
  }), [pathname])
  return <LocationContext.Provider value={value}>{children}</LocationContext.Provider>
}

export function useLocation() {
  const context = useContext(LocationContext)
  if (!context) throw new Error('useLocation must be used inside BrowserRouter')
  return { pathname: context.pathname }
}

export function useNavigate() {
  const context = useContext(LocationContext)
  if (!context) throw new Error('useNavigate must be used inside BrowserRouter')
  return context.navigate
}

type LinkProps = React.AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }
export function Link({ to, onClick, children, ...props }: LinkProps) {
  const navigate = useNavigate()
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || props.target) return
    event.preventDefault()
    navigate(to)
  }
  return <a href={to} onClick={handleClick} {...props}>{children}</a>
}

type NavLinkProps = Omit<LinkProps, 'className'> & { className?: string | ((state: { isActive: boolean }) => string) }
export function NavLink({ className, to, ...props }: NavLinkProps) {
  const { pathname } = useLocation()
  const resolved = typeof className === 'function' ? className({ isActive: pathname === to }) : className
  return <Link to={to} className={resolved} {...props} />
}

type RouteProps = { path: string; element: ReactElement }
export function Route(_: RouteProps) { return null }

function match(pathname: string, pattern: string): Record<string, string> | null {
  if (pattern === '*') return {}
  const pathParts = pathname.replace(/^\/+|\/+$/g, '').split('/').filter(Boolean)
  const patternParts = pattern.replace(/^\/+|\/+$/g, '').split('/').filter(Boolean)
  if (pathParts.length !== patternParts.length) return null
  const params: Record<string, string> = {}
  for (let index = 0; index < patternParts.length; index += 1) {
    const part = patternParts[index]
    if (part.startsWith(':')) params[part.slice(1)] = decodeURIComponent(pathParts[index])
    else if (part !== pathParts[index]) return null
  }
  return params
}

export function Routes({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  for (const child of Children.toArray(children)) {
    if (!isValidElement<RouteProps>(child)) continue
    const params = match(pathname, child.props.path)
    if (params) return <ParamsContext.Provider value={params}>{child.props.element}</ParamsContext.Provider>
  }
  return null
}

export function useParams<T extends Record<string, string | undefined> = Record<string, string>>() {
  return useContext(ParamsContext) as T
}

export function Navigate({ to, replace = false }: { to: string; replace?: boolean }) {
  const navigate = useNavigate()
  useEffect(() => navigate(to, replace), [navigate, replace, to])
  return null
}
