
import React from 'react';
import DeleteBatchDialog from './DeleteBatchDialog';

interface ImageBatchActionsProps {
  showDeleteDialog: boolean;
  onDeleteDialogClose: () => void;
  onConfirmDelete: () => void;
}

const ImageBatchActions: React.FC<ImageBatchActionsProps> = ({
  showDeleteDialog,
  onDeleteDialogClose,
  onConfirmDelete
}) => {
  return (
    <DeleteBatchDialog 
      isOpen={showDeleteDialog}
      onClose={onDeleteDialogClose}
      onConfirm={onConfirmDelete}
    />
  );
};

export default ImageBatchActions;
