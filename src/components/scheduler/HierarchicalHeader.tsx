import React from 'react';
import { CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useIsMobile } from '@/hooks/useIsMobile';

interface HierarchicalHeaderProps {
  title: string;
  level: 1 | 2 | 3;
  isOpen: boolean;
  onToggle: () => void;
  count?: number;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
}

export const HierarchicalHeader: React.FC<HierarchicalHeaderProps> = ({ 
  title, 
  level,
  isOpen, 
  onToggle,
  count,
  icon,
  actions
}) => {
  const isMobile = useIsMobile();
  
  // Define styles based on hierarchy level
  const getHeaderStyles = () => {
    const baseStyles = "cursor-pointer hover:bg-muted/50 transition-colors";
    
    switch (level) {
      case 1:
        return {
          headerClass: `${baseStyles} ${isMobile ? 'p-3' : 'p-6'}`,
          titleClass: "text-lg sm:text-xl font-bold flex items-center",
          iconSize: "h-4 w-4 sm:h-5 sm:w-5",
          badgeClass: "ml-2 text-xs"
        };
      case 2:
        return {
          headerClass: `${baseStyles} ${isMobile ? 'p-2' : 'p-4'} bg-muted/20`,
          titleClass: "text-base sm:text-lg font-semibold flex items-center",
          iconSize: "h-4 w-4",
          badgeClass: "ml-2 text-xs"
        };
      case 3:
        return {
          headerClass: `${baseStyles} ${isMobile ? 'p-2' : 'p-3'} bg-muted/10`,
          titleClass: "text-sm sm:text-base font-medium flex items-center",
          iconSize: "h-3 w-3 sm:h-4 sm:w-4",
          badgeClass: "ml-1 text-xs"
        };
      default:
        return {
          headerClass: baseStyles,
          titleClass: "flex items-center",
          iconSize: "h-4 w-4",
          badgeClass: "ml-2 text-xs"
        };
    }
  };
  
  const styles = getHeaderStyles();
  
  return (
    <CardHeader 
      className={styles.headerClass}
      onClick={onToggle}
    >
      <div className="flex items-center justify-between">
        <CardTitle className={styles.titleClass}>
          {isOpen ? (
            <ChevronDown className={`${styles.iconSize} mr-2 text-muted-foreground`} />
          ) : (
            <ChevronRight className={`${styles.iconSize} mr-2 text-muted-foreground`} />
          )}
          {icon && <span className="mr-2">{icon}</span>}
          {title}
          {count !== undefined && (
            <Badge variant="secondary" className={styles.badgeClass}>
              {count}
            </Badge>
          )}
        </CardTitle>
        {actions && (
          <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
            {actions}
          </div>
        )}
      </div>
    </CardHeader>
  );
}; 