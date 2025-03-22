
import * as React from "react"

const MOBILE_BREAKPOINT = 768

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean>(() => {
    // Initial detection based on both screen size and user agent
    return typeof window !== 'undefined' && (
      window.innerWidth < MOBILE_BREAKPOINT || 
      /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
    );
  });

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    }
    
    // Use the newer addEventListener API with a fallback
    if (mql.addEventListener) {
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    } else {
      // Fallback for older browsers
      mql.addListener(onChange);
      return () => mql.removeListener(onChange);
    }
  }, []);

  return isMobile;
}

export function useWindowSize() {
  const [windowSize, setWindowSize] = React.useState<{
    width: number | undefined
    height: number | undefined
  }>({
    width: typeof window !== 'undefined' ? window.innerWidth : undefined,
    height: typeof window !== 'undefined' ? window.innerHeight : undefined,
  })

  React.useEffect(() => {
    function handleResize() {
      setWindowSize({
        width: window.innerWidth,
        height: window.innerHeight,
      })
    }
    
    window.addEventListener("resize", handleResize)
    handleResize()
    
    return () => window.removeEventListener("resize", handleResize)
  }, [])

  return windowSize
}

// Helper to detect touch devices
export function useIsTouchDevice() {
  const [isTouch, setIsTouch] = React.useState<boolean>(() => {
    // Initial detection based on browser features
    return typeof window !== 'undefined' && (
      'ontouchstart' in window || 
      navigator.maxTouchPoints > 0 ||
      (navigator as any).msMaxTouchPoints > 0
    );
  });

  return isTouch;
}
