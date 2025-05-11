import React from 'react';
import { ChevronDown, ChevronRight, MoreVertical } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';

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
  /** Whether to show a context menu with the "..." button */
  showContextMenu?: boolean;
  /** Menu items for the context menu */
  contextMenuItems?: { 
    label: string; 
    onClick: () => void; 
    icon?: React.ReactNode;
    variant?: 'default' | 'destructive';
  }[];
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
  showContextMenu = false,
  contextMenuItems = [],
  children,
}, ref) => {
  const handleToggle = React.useCallback(() => onToggle(id), [id, onToggle]);

  // choose arrow orientation based on collapsed state
  const ArrowIcon = collapsed ? ChevronRight : ChevronDown;

  // wrapper classes for different variants
  const base = variant === 'panel'
    ? 'rounded-lg border bg-card w-full' // generated panel look
    : 'border-b last:border-b-0 w-full'; // accordion-like section look

  const headerPadding = variant === 'panel' ? 'py-1 px-2' : 'py-2 px-3';

  return (
    <div ref={ref} className={`${base} ${className}`.trim()}>
      {/* Header */}
      <div className="flex items-center w-full text-left select-none">
        <button
          type="button"
          onClick={handleToggle}
          className={`flex items-center text-left ${headerPadding}`}
        >
          {iconPos === 'left' && (
            <ArrowIcon className="h-4 w-4 mr-1 shrink-0" />
          )}
          {label && <span className="truncate text-sm font-medium">{label}</span>}
          {iconPos === 'right' && (
            <ArrowIcon className="h-4 w-4 ml-1 shrink-0" />
          )}
        </button>
        
        <div className="flex-1"></div>
        
        {/* Extras (e.g., sort toggle) */}
        {headerExtras && (
          <span className="ml-2" onClick={(e) => e.stopPropagation()}>
            {headerExtras}
          </span>
        )}
        
        {/* Context menu */}
        {showContextMenu && contextMenuItems && contextMenuItems.length > 0 && (
          <div onClick={(e) => e.stopPropagation()}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 p-0 ml-2 text-muted-foreground hover:text-foreground"
                >
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {contextMenuItems.map((item, index) => (
                  <DropdownMenuItem
                    key={index}
                    onClick={item.onClick}
                    className={`flex items-center ${item.variant === 'destructive' ? 'text-destructive' : ''}`}
                  >
                    {item.icon && <span className="mr-2">{item.icon}</span>}
                    {item.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </div>

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