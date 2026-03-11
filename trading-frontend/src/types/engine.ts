export type EngineState = 
    | "IDLE"
    | "PREVIEWING"
    | "OPEN"
    | "CLOSING"
    | "ERROR";

export interface Position { 
    id? : string;
    [key : string] : unknown;
}

export type Preview = {
  estimated_margin: number;
  max_loss: number;
  expires_at: string;
};


export interface EngineStatus {
    engine_state : EngineState;
    mode : string;
    position : Position | null;
    preview : Preview | null;
    last_update_ts : string | null ;
    net_pnl : number;
    error_message? : string | null;
}

