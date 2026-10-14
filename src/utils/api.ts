// Compatibility facade for the old `Api` singleton. The calls themselves live in src/shared/api/.
// New code should import the domain modules instead: `import { bucketsApi } from '@/shared/api'`.
// Only src/pages/DisplayPage (peer-owned) still imports it; once that moves over, delete this file (REFACTOR_PLAN T7).
import {
  API_URL,
  bucketsApi,
  eventsApi,
  filesApi,
  generationApi,
  lightApi,
  logsApi,
  publishApi,
  schedulerApi,
  varsApi,
} from '@/shared/api';

export type { Bucket, BucketItem, PublishDestination, ReferenceImageInfo } from '@/shared/api/types';

/** @deprecated Import the domain modules from '@/shared/api' instead. */
export class Api {
  private static instance: Api | null = null;

  constructor() {
    // `new Api()` always returns the shared instance; DisplayPage relies on that.
    if (Api.instance) {
      return Api.instance;
    }
    Api.instance = this;
  }

  getApiUrl(): string {
    return API_URL;
  }

  generateImage = generationApi.generateImage;
  cancelAllJobs = generationApi.cancelAllJobs;

  getLogs = logsApi.getLogs;

  publishImageUnified = publishApi.publishImageUnified;
  getPublishDestinations = publishApi.getPublishDestinations;
  getPublishedContent = publishApi.getPublishedContent;
  getPublishedContentForDestination = publishApi.getPublishedContentForDestination;

  getSchedulerSchema = schedulerApi.getSchedulerSchema;
  listSchedulers = schedulerApi.listSchedulers;
  getDestinations = schedulerApi.getDestinations;
  getSchedulerLogs = schedulerApi.getSchedulerLogs;
  startScheduler = schedulerApi.startScheduler;
  stopScheduler = schedulerApi.stopScheduler;
  getSchedule = schedulerApi.getSchedule;
  pauseScheduler = schedulerApi.pauseScheduler;
  unpauseScheduler = schedulerApi.unpauseScheduler;
  getSchedulerStatus = schedulerApi.getSchedulerStatus;
  getSchedulerContext = schedulerApi.getSchedulerContext;
  getInstructionQueue = schedulerApi.getInstructionQueue;
  unloadSchedule = schedulerApi.unloadSchedule;
  getScheduleStack = schedulerApi.getScheduleStack;
  setSchedulerContextVar = schedulerApi.setSchedulerContextVar;
  getAllSchedulerStatuses = schedulerApi.getAllSchedulerStatuses;
  getNextScheduledAction = schedulerApi.getNextScheduledAction;
  getScheduleAtPosition = schedulerApi.getScheduleAtPosition;

  throwEvent = eventsApi.throwEvent;
  getEventsForDestination = eventsApi.getEventsForDestination;
  getAllEvents = eventsApi.getAllEvents;
  clearEventByKey = eventsApi.clearEventByKey;
  clearEventById = eventsApi.clearEventById;
  clearAllEvents = eventsApi.clearAllEvents;

  getVarsRegistry = varsApi.getVarsRegistry;
  setExportedVar = varsApi.setExportedVar;
  deleteExportedVar = varsApi.deleteExportedVar;

  deleteFromBucket = bucketsApi.deleteFromBucket;
  purgeBucket = bucketsApi.purgeBucket;
  moveToPosition = bucketsApi.moveToPosition;
  getBucketDetails = bucketsApi.getBucketDetails;
  getImageComplete = bucketsApi.getImageComplete;
  toggleFavorite = bucketsApi.toggleFavorite;
  deleteImage = bucketsApi.deleteImage;
  copyImageToBucket = bucketsApi.copyImageToBucket;
  performBucketMaintenance = bucketsApi.performBucketMaintenance;

  listFiles = filesApi.listFiles;
  readFile = filesApi.readFile;
  writeFile = filesApi.writeFile;

  getMask = lightApi.getMask;
  enableMask = lightApi.enableMask;
  disableMask = lightApi.disableMask;
  getMaskState = lightApi.getMaskState;
  getLightsense = lightApi.getLightsense;
  getIntensitySettings = lightApi.getIntensitySettings;
  updateIntensitySettings = lightApi.updateIntensitySettings;
}

const apiService = new Api();

export default apiService;
