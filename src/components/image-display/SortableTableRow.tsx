
import React from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { TableRow, TableCell } from '@/components/ui/table';
import { Image } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

interface SortableTableRowProps {
  id: string;
  onClick: () => void;
  index: number;
  prompt: string;
  hasReferenceImage: boolean;
  completedImages: number;
  timestamp: number;
}

const SortableTableRow: React.FC<SortableTableRowProps> = ({
  id,
  onClick,
  index,
  prompt,
  hasReferenceImage,
  completedImages,
  timestamp
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
      className="hover:bg-muted/60 cursor-grab active:cursor-grabbing"
      onClick={onClick}
      {...attributes}
      {...listeners}
    >
      <TableCell className="font-medium">{index}</TableCell>
      <TableCell>
        <div className="flex items-start">
          {hasReferenceImage && (
            <Image className="h-4 w-4 text-primary mr-2 mt-1 flex-shrink-0" />
          )}
          <span className="break-words whitespace-normal max-w-[200px] md:max-w-[300px]">{prompt}</span>
        </div>
      </TableCell>
      <TableCell className="text-center">{completedImages}</TableCell>
      <TableCell>{formatTimeAgo(timestamp)}</TableCell>
    </TableRow>
  );
};

export default SortableTableRow;
