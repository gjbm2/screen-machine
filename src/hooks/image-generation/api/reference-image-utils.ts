
/**
 * Processes uploaded files and returns arrays of files and URLs
 */
export const processUploadedFiles = (
  imageFiles?: (File | string)[]
): { uploadedFiles: File[], uploadedImageUrls: string[] } => {
  let uploadedFiles: File[] = [];
  let uploadedImageUrls: string[] = [];
  
  if (imageFiles && imageFiles.length > 0) {
    for (const file of imageFiles) {
      if (typeof file === 'string') {
        uploadedImageUrls.push(file);
      } else {
        // For File objects, create an object URL
        const objectUrl = URL.createObjectURL(file);
        uploadedImageUrls.push(objectUrl);
        uploadedFiles.push(file);
        console.log('[reference-image-utils] Created object URL for file:', objectUrl);
      }
    }
  }
  
  console.log('[reference-image-utils] Processed files:', uploadedFiles.length, 'and URLs:', uploadedImageUrls);
  
  return { uploadedFiles, uploadedImageUrls };
};
