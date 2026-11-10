export interface ClientConfig {
    pythonExecutable: string;
    root: string;
    requestTimeoutMs: number;
}
export interface Invocation {
    session_id: string;
    call_id: string;
}
export type Json = null | boolean | number | string | Json[] | {
    [key: string]: Json;
};
export type JsonObject = {
    [key: string]: Json;
};
export declare class BackendClient {
    private readonly config;
    private readonly lifetime;
    private readonly active;
    constructor(config: ClientConfig);
    call(operation: string, args: Record<string, unknown>, signal: AbortSignal, invocation?: Invocation): Promise<JsonObject>;
    dispose(): Promise<void>;
    private request;
}
