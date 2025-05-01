import { toast } from "sonner";

export interface BucketItem {
  filename: string;
  thumbnail: string;
  isFavorite: boolean;
  index: number;
  bucket: string;
}

export interface Bucket {
  name: string;
  items: BucketItem[];
  published?: string;
  publishedAt?: string;
}

// Functions to interact with the bucket API
export async function fetchAllBuckets(): Promise<string[]> {
  try {
    console.log('Fetching all buckets from /test-buckets/ endpoint');
    const response = await fetch('/test-buckets/');
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    const text = await response.text();
    console.log('Got response from buckets endpoint, parsing HTML');
    
    // Extract bucket names from HTML response
    const bucketRegex = /<a href="\/test-buckets\/([^\/]+)\/">/g;
    const buckets: string[] = [];
    let match;
    
    while ((match = bucketRegex.exec(text)) !== null) {
      buckets.push(match[1]);
    }
    
    console.log('Found buckets:', buckets);
    return buckets;
  } catch (error) {
    console.error('Error fetching buckets:', error);
    toast.error('Failed to load buckets');
    return [];
  }
}

export async function fetchBucketDetails(bucketName: string): Promise<Bucket> {
  try {
    console.log(`Fetching details for bucket: ${bucketName}`);
    
    // First try the new API endpoint that includes embedded thumbnails
    const apiResponse = await fetch(`/api/buckets/${bucketName}/items`);
    
    if (apiResponse.ok) {
      const bucketData = await apiResponse.json();
      console.log(`Got bucket data from API for ${bucketName}`);
      
      // Check if the response includes embedded thumbnails
      if (bucketData.items_with_thumbnails && bucketData.items_with_thumbnails.length > 0) {
        console.log(`Using ${bucketData.items_with_thumbnails.length} embedded thumbnails`);
        
        // Map the items_with_thumbnails to our BucketItem format
        const items: BucketItem[] = bucketData.items_with_thumbnails.map((item, index) => ({
          filename: item.filename,
          thumbnail: item.thumbnail_embedded || `/api/buckets/${bucketName}/thumbnail/${item.filename}`,
          isFavorite: bucketData.favorites ? bucketData.favorites.includes(item.filename) : false,
          index,
          bucket: bucketName
        }));
        
        return {
          name: bucketName,
          items,
          published: bucketData.published_meta?.filename,
          publishedAt: bucketData.published_meta?.published_at
        };
      }
    }
    
    // Fall back to the old method if the API endpoint fails or doesn't include thumbnails
    console.log(`Falling back to old method for bucket ${bucketName}`);
    const response = await fetch(`/test-buckets/${bucketName}/`);
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    const text = await response.text();
    
    // Extract sequence and favorites
    const sequenceMatch = text.match(/var sequence = (\[[^\]]+\]);/);
    const favoritesMatch = text.match(/var favorites = (\[[^\]]+\]);/);
    const publishedMatch = text.match(/<strong>Currently published:<\/strong>\s*<a[^>]*>([^<]+)<\/a>/);
    const publishedAtMatch = text.match(/<small>Published at ([^<]+)<\/small>/);
    
    console.log('Parsed HTML, extracting sequence and favorites');
    
    let sequence: string[] = [];
    let favorites: string[] = [];
    
    if (sequenceMatch && sequenceMatch[1]) {
      try {
        sequence = JSON.parse(sequenceMatch[1]);
        console.log(`Found ${sequence.length} images in sequence`);
      } catch (e) {
        console.error('Failed to parse sequence:', e);
      }
    }
    
    if (favoritesMatch && favoritesMatch[1]) {
      try {
        favorites = JSON.parse(favoritesMatch[1]);
        console.log(`Found ${favorites.length} favorites`);
      } catch (e) {
        console.error('Failed to parse favorites:', e);
      }
    }
    
    const items: BucketItem[] = sequence.map((filename, index) => ({
      filename,
      thumbnail: `/test-buckets/${bucketName}/thumbnail/${filename}`,
      isFavorite: favorites.includes(filename),
      index,
      bucket: bucketName
    }));
    
    return {
      name: bucketName,
      items,
      published: publishedMatch ? publishedMatch[1] : undefined,
      publishedAt: publishedAtMatch ? publishedAtMatch[1] : undefined
    };
  } catch (error) {
    console.error('Error fetching bucket details:', error);
    toast.error(`Failed to load details for bucket: ${bucketName}`);
    return { name: bucketName, items: [] };
  }
}

export async function toggleFavorite(bucket: string, filename: string, currentState: boolean): Promise<boolean> {
  try {
    console.log(`${currentState ? 'Unfavoriting' : 'Favoriting'} image ${filename} in bucket ${bucket}`);
    const formData = new FormData();
    formData.append('filename', filename);
    formData.append('action', currentState ? 'unfavorite' : 'favorite');
    
    const response = await fetch(`/test-buckets/${bucket}/`, {
      method: 'POST',
      body: formData
    });
    
    if (response.ok) {
      toast.success(`${currentState ? 'Unfavorited' : 'Favorited'} image`);
      return true;
    } else {
      throw new Error(`Server returned ${response.status}`);
    }
  } catch (error) {
    console.error('Error toggling favorite:', error);
    toast.error('Failed to update favorite status');
    return false;
  }
}

export async function deleteImage(bucket: string, filename: string): Promise<boolean> {
  try {
    const formData = new FormData();
    formData.append('filename', filename);
    formData.append('action', 'delete');
    
    const response = await fetch(`/test-buckets/${bucket}/`, {
      method: 'POST',
      body: formData
    });
    
    if (response.ok) {
      toast.success('Image deleted');
      return true;
    } else {
      throw new Error(`Server returned ${response.status}`);
    }
  } catch (error) {
    console.error('Error deleting image:', error);
    toast.error('Failed to delete image');
    return false;
  }
}

export async function moveImage(bucket: string, filename: string, direction: 'up' | 'down'): Promise<boolean> {
  try {
    const formData = new FormData();
    formData.append('filename', filename);
    formData.append('action', direction === 'up' ? 'move-up' : 'move-down');
    
    const response = await fetch(`/test-buckets/${bucket}/`, {
      method: 'POST',
      body: formData
    });
    
    if (response.ok) {
      return true;
    } else {
      throw new Error(`Server returned ${response.status}`);
    }
  } catch (error) {
    console.error(`Error moving image ${direction}:`, error);
    toast.error(`Failed to move image ${direction}`);
    return false;
  }
}

export async function copyImageToBucket(sourceBucket: string, targetBucket: string, filename: string): Promise<boolean> {
  try {
    const formData = new FormData();
    formData.append('filename', filename);
    formData.append('action', 'copy');
    formData.append('dest_bucket', targetBucket);
    
    const response = await fetch(`/test-buckets/${sourceBucket}/`, {
      method: 'POST',
      body: formData
    });
    
    if (response.ok) {
      toast.success(`Image copied to ${targetBucket}`);
      return true;
    } else {
      throw new Error(`Server returned ${response.status}`);
    }
  } catch (error) {
    console.error('Error copying image:', error);
    toast.error('Failed to copy image');
    return false;
  }
}

export async function publishImage(bucket: string, filename: string): Promise<boolean> {
  try {
    const formData = new FormData();
    formData.append('filename', filename);
    formData.append('action', 'publish');
    
    const response = await fetch(`/test-buckets/${bucket}/`, {
      method: 'POST',
      body: formData
    });
    
    if (response.ok) {
      toast.success('Image published');
      return true;
    } else {
      throw new Error(`Server returned ${response.status}`);
    }
  } catch (error) {
    console.error('Error publishing image:', error);
    toast.error('Failed to publish image');
    return false;
  }
}

export async function performBucketMaintenance(bucket: string, action: 'purge' | 'reindex' | 'extract'): Promise<boolean> {
  try {
    const formData = new FormData();
    formData.append('maintenance', action);
    
    const response = await fetch(`/test-buckets/${bucket}/`, {
      method: 'POST',
      body: formData
    });
    
    if (response.ok) {
      toast.success(`Bucket ${action} completed`);
      return true;
    } else {
      throw new Error(`Server returned ${response.status}`);
    }
  } catch (error) {
    console.error(`Error performing bucket maintenance (${action}):`, error);
    toast.error(`Failed to ${action} bucket`);
    return false;
  }
}
