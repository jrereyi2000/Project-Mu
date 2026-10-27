import { createContext } from "react";

export type SystemStatus = {
  season: number;
  week: number;
  availableSeasons: number[];
  last_updated?: string | null;
};

export const SystemContext = createContext<SystemStatus>({
  season: 2035,
  week: 18,
  availableSeasons: [2035],
});
