async getSchedulerLogs(publishDestination: string): Promise<any> {
  const response = await this.api.get(`/api/schedulers/${publishDestination}/logs`);
  return response.data;
}

async getNextScheduledAction(publishDestination: string): Promise<any> {
  const response = await this.api.get(`/api/schedulers/${publishDestination}/next_action`);
  return response.data;
} 