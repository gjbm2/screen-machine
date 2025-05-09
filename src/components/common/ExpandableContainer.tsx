import React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

export type ExpandableVariant = 'panel' | 'section';

export interface ExpandableContainerProps {
  id: string;
  label?: string;
  /** Where to place the expand / collapse arrow.  'left' for section lists (bucket); 'right' for generated panels. */
  iconPos?: 'left' | 'right';
  variant?: ExpandableVariant;
  collapsed: boolean;
  onToggle: (id: string) => void;
  /** Additional class names for wrapper. */
  className?: string;
  /** Extra content (e.g., sort toggles) shown in the header next to the label.  Clicks will not collapse. */
  headerExtras?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * Simple reusable expandable/collapsible container.  Visual styling is delegated to the
 * `variant` prop so we can later override via CSS without touching the behaviour.
 */
export const ExpandableContainer = React.forwardRef<HTMLDivElement, ExpandableContainerProps>(({
  id,
  label,
  iconPos = 'right',
  variant = 'panel',
  collapsed,
  onToggle,
  className = '',
  headerExtras,
  children,
}, ref) => {
  const handleToggle = React.useCallback(() => onToggle(id), [id, onToggle]);

  // choose arrow orientation based on collapsed state
  const ArrowIcon = collapsed ? ChevronRight : ChevronDown;

  // wrapper classes for different variants
  const base = variant === 'panel'
    ? 'rounded-lg border bg-card' // generated panel look
    : 'border-b last:border-b-0'; // accordion-like section look

  const headerPadding = variant === 'panel' ? 'py-1 px-2' : 'py-2 px-3';

  return (
    <div ref={ref} className={`${base} ${className}`.trim()}>
      {/* Header */}
      <button
        type="button"
        onClick={handleToggle}
        className={`flex items-center w-full text-left select-none ${headerPadding}`}
      >
        {iconPos === 'left' && (
          <ArrowIcon className="h-4 w-4 mr-1 shrink-0" />
        )}
        {label && <span className="flex-1 truncate text-sm font-medium">{label}</span>}
        {/* Extras (e.g., sort toggle).  Stop click propagation so container doesn't collapse. */}
        {headerExtras && (
          <span className="ml-2" onClick={(e) => e.stopPropagation()}>
            {headerExtras}
          </span>
        )}
        {iconPos === 'right' && (
          <ArrowIcon className="h-4 w-4 ml-1 shrink-0" />
        )}
      </button>

      {/* Content area – mount/unmount for performance */}
      {!collapsed && (
        <div className={variant === 'panel' ? 'p-1' : 'pl-7 pr-3 pb-2'}>
          {children}
        </div>
      )}
    </div>
  );
});

export default ExpandableContainer; 