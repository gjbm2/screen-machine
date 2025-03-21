
import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Table, TableBody, TableRow, TableCell } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Trash2 } from 'lucide-react';

interface TableBatchViewProps {
  batchId: string;
  completedImages: Array<any>;
  onImageClick: (url: string, prompt: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
}

const TableBatchView: React.FC<TableBatchViewProps> = ({
  batchId,
  completedImages,
  onImageClick,
  onDeleteImage
}) => {
  return (
    <Card className="rounded-t-none">
      <CardContent className="p-1">
        <Table>
          <TableBody>
            {completedImages.map((image, index) => (
              <TableRow key={`${batchId}-${index}`}>
                <TableCell className="p-1">
                  <div className="w-16 h-16 overflow-hidden rounded">
                    <img 
                      src={image.url}
                      alt={image.prompt}
                      className="w-full h-full object-cover"
                      onClick={() => onImageClick(image.url, image.prompt)}
                    />
                  </div>
                </TableCell>
                <TableCell className="p-1">
                  <p className="text-xs truncate text-muted-foreground max-w-md">{image.prompt}</p>
                </TableCell>
                <TableCell className="p-1 text-right">
                  <Button 
                    variant="ghost" 
                    size="icon" 
                    className="h-8 w-8"
                    onClick={() => onDeleteImage(batchId, image.batchIndex)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
};

export default TableBatchView;
