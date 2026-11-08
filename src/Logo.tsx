import logo from './assets/branding/logo.png'
import logo2x from './assets/branding/logo@2x.png'

export function Logo() {
  return <img className="brand-logo" src={logo} srcSet={`${logo2x} 2x`} width={64} height={64} alt="" aria-hidden="true" />
}
