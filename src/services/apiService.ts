export interface DisplayFromBucketRequest {
  mode: 'Next' | 'Random' | 'Blank';
  silent?: boolean;
}

export interface DisplayFromBucketResponse {
  status: string;
  message: string;
}

export const displayFromBucket = async (
  publishDestinationId: string,
  request: DisplayFromBucketRequest
): Promise<DisplayFromBucketResponse> => {
  try {
    const response = await fetch(`/api/publish/${publishDestinationId}/display`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || 'Failed to display image');
    }

    return await response.json();
  } catch (error) {
    console.error('Error displaying from bucket:', error);
    throw error;
  }
}; 