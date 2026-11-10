import type { Context } from '@deepseek-ai/cordis';
import z from '@deepseek-ai/schemastery';
export declare const name = "label-workbench";
export declare const inject: string[];
export interface Config {
    pythonExecutable: string;
    root: string;
    requestTimeoutMs: number;
}
export declare const Config: z<Config>;
export declare function apply(ctx: Context, config: Config): void;
