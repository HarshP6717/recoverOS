import axios from 'axios';
import type {
  HealthResponse,
  DashboardOverviewResponse,
  JourneyListResponse,
  JourneyDetailResponse,
  JourneyTimelineResponse
} from './types';

const apiBase = axios.create({
  baseURL: 'http://127.0.0.1:8000',
  timeout: 10000,
});

const apiClient = axios.create({
  baseURL: 'http://127.0.0.1:8000/v1/dashboard',
  timeout: 10000,
});

export const getHealth = async (): Promise<HealthResponse> => {
  const { data } = await apiBase.get('/health');
  return data;
};

export const getOverview = async (): Promise<DashboardOverviewResponse> => {
  const { data } = await apiClient.get('/overview');
  return data;
};

export const getJourneys = async (params?: {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<JourneyListResponse> => {
  const { data } = await apiClient.get('/journeys', { params });
  return data;
};

export const getJourneyDetail = async (journeyId: string): Promise<JourneyDetailResponse> => {
  const { data } = await apiClient.get(`/journeys/${journeyId}`);
  return data;
};

export const getJourneyTimeline = async (journeyId: string): Promise<JourneyTimelineResponse> => {
  const { data } = await apiClient.get(`/journeys/${journeyId}/timeline`);
  return data;
};
