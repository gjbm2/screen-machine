import React from 'react';
import { ChevronDown, ChevronRight, MoreVertical, MoreHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';

export type ExpandableVariant = 'panel' | 'section';

export interface ExpandableContainerProps {
  id: string;
  /** The main header, may be a plain string or composed JSX (e.g. truncated prompt + counter). */
  label?: React.ReactNode;
  /** Where to place the expand / collapse arrow.  'left' for section lists (bucket); 'right' for generated panels. */
  iconPos?: 'left' | 'right';
  variant?: ExpandableVariant;
  collapsed: boolean;
  onToggle: (id: string) => void;
  /** Additional class names for wrapper. */
  className?: string;
  /** Content to display at the start of the header, before the label and toggle icon if iconPos is 'right'. */
  headerStart?: React.ReactNode;
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
  headerStart,
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
        {/* Header Start Content */}
        {headerStart}

        {/* Center section with label */}
        <button
          type="button"
          onClick={handleToggle}
          className={`expand-toggle flex items-center text-left ${headerPadding} min-w-0 flex-1 overflow-hidden`}
        >
          {iconPos === 'left' && (
            <ArrowIcon className="h-4 w-4 mr-1 shrink-0" />
          )}
          {label && (
            <span className="flex items-center min-w-0 max-w-full text-sm font-medium">
              {/* If label is a string, wrap it in a truncating span so ellipsis only affects the prompt text. */}
              {typeof label === 'string' ? (
                <span className="truncate max-w-full" style={{ minWidth: 0 }}>{label}</span>
              ) : (
                label
              )}
            </span>
          )}
        </button>

        {/* Right side extras */}
        <div className="flex items-center shrink-0">
          {headerExtras}
          
          {/* Context menu */}
          {showContextMenu && contextMenuItems && contextMenuItems.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="ml-1 rounded-md p-1 hover:bg-accent text-muted-foreground"
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreHorizontal className="h-4 w-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {contextMenuItems.map((item, i) => (
                  <DropdownMenuItem
                    key={i}
                    onClick={(e) => {
                      e.stopPropagation();
                      item.onClick();
                    }}
                    className={item.variant === 'destructive' ? 'text-destructive' : ''}
                  >
                    {item.icon && <span className="mr-2">{item.icon}</span>}
                    {item.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          {/* Right arrow placed after context menu when iconPos is right */}
          {iconPos === 'right' && (
            <button
              type="button"
              onClick={handleToggle}
              className="ml-1 p-1 hover:bg-accent rounded-md text-muted-foreground"
            >
              <ArrowIcon className="h-4 w-4" />
            </button>
          )}
        </div>
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