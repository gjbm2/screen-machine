
import React from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { TableRow, TableCell } from '@/components/ui/table';
import { Image } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

interface SortableTableRowProps {
  id: string;
  onClick: () => void;
  prompt: string;
  workflow: string;
  hasReferenceImage: boolean;
  completedImages: number;
  timestamp: number;
  title?: string;
}

const SortableTableRow: React.FC<SortableTableRowProps> = ({
  id,
  onClick,
  prompt,
  workflow,
  hasReferenceImage,
  completedImages,
  timestamp,
  title
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging
  } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    zIndex: isDragging ? 10 : 1,
    cursor: 'pointer'
  };

  const formatTimeAgo = (timestamp: number) => {
    if (!timestamp) return "Unknown";
    return formatDistanceToNow(new Date(timestamp), { addSuffix: true });
  };

  return (
    <TableRow 
      ref={setNodeRef} 
      style={style}
      className="hover:bg-muted/60 cursor-grab active:cursor-grabbing text-xs"
      onClick={onClick}
      {...attributes}
      {...listeners}
    >
      <TableCell className="py-0.5 px-1">
        <div className="flex items-start max-w-[180px] md:max-w-[240px]">
          {hasReferenceImage && (
            <Image className="h-3 w-3 text-primary mr-1 mt-1 flex-shrink-0" />
          )}
          <span className="break-words whitespace-normal text-xs">{prompt}</span>
        </div>
      </TableCell>
      <TableCell className="py-0.5 px-1 text-xs">{workflow}</TableCell>
      <TableCell className="text-center py-0.5 px-1">{completedImages}</TableCell>
      <TableCell className="py-0.5 px-1 text-xs">{formatTimeAgo(timestamp)}</TableCell>
    </TableRow>
  );
};

export default SortableTableRow;
