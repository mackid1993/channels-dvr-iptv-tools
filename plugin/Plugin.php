<?php

namespace AppLocalPlugins\GracenoteStationIds;

use App\Models\Channel;
use App\Plugins\Contracts\ChannelProcessorPluginInterface;
use App\Plugins\Contracts\PluginInterface;
use App\Plugins\Support\PluginActionResult;
use App\Plugins\Support\PluginExecutionContext;

/**
 * Writes station IDs chosen in the Channels DVR Station ID Matcher.
 *
 * m3u-editor's REST API cannot set a channel's Gracenote station ID, so the
 * matcher copies its picks as "channel_id=station_id" lines and this action
 * applies them. Nothing is written unless every line is valid, and only
 * channels that belong to the selected playlist are touched.
 */
class Plugin implements ChannelProcessorPluginInterface, PluginInterface
{
    public function runAction(string $action, array $payload, PluginExecutionContext $context): PluginActionResult
    {
        return match ($action) {
            'apply_station_ids' => $this->apply($payload, $context),
            default => PluginActionResult::failure("Unsupported action [{$action}]."),
        };
    }

    private function apply(array $payload, PluginExecutionContext $context): PluginActionResult
    {
        $playlistId = (int) ($payload['playlist_id'] ?? 0);
        if ($playlistId === 0) {
            return PluginActionResult::failure('Pick the playlist the station IDs belong to.');
        }

        [$wanted, $errors] = $this->parse((string) ($payload['station_ids'] ?? ''));
        if ($errors !== []) {
            return PluginActionResult::failure(
                'Nothing was written. Fix these lines and run it again: '.implode('; ', array_slice($errors, 0, 10)),
                ['errors' => $errors]
            );
        }
        if ($wanted === []) {
            return PluginActionResult::failure('No station IDs were pasted.');
        }

        $query = Channel::query()->where('playlist_id', $playlistId)->whereIn('id', array_keys($wanted));
        if ($context->user) {
            $query->where('user_id', $context->user->id);
        }
        $found = $query->pluck('id')->map(fn ($id) => (int) $id)->all();
        $missing = array_values(array_diff(array_keys($wanted), $found));
        foreach ($missing as $id) {
            $context->warning("Channel #{$id} is not in this playlist (deleted, or a different playlist). Skipped.");
        }

        $written = 0;
        foreach ($found as $i => $id) {
            if ($context->dryRun) {
                continue;
            }
            $sid = $wanted[$id];
            Channel::query()->whereKey($id)->update(['station_id' => $sid === '' ? null : $sid]);
            $written++;
            if (($i + 1) % 100 === 0) {
                $context->heartbeat(progress: (int) ((($i + 1) / count($found)) * 100));
            }
        }

        // Read back every channel and confirm it holds the requested value.
        $mismatched = [];
        if (! $context->dryRun && $found !== []) {
            $now = Channel::query()->whereIn('id', $found)->pluck('station_id', 'id');
            foreach ($found as $id) {
                if ((string) ($now[$id] ?? '') !== $wanted[$id]) {
                    $mismatched[] = $id;
                }
            }
        }

        $summary = sprintf('Wrote %d station IDs to playlist #%d.', $written, $playlistId);
        if ($missing !== []) {
            $summary .= sprintf(' %d channels were not in the playlist and were skipped.', count($missing));
        }
        if ($mismatched !== []) {
            return PluginActionResult::failure(
                $summary.' '.count($mismatched).' did not read back correctly: #'.implode(', #', $mismatched),
                ['written' => $written, 'skipped' => $missing, 'mismatched' => $mismatched]
            );
        }

        return PluginActionResult::success($summary, [
            'written' => $written,
            'skipped' => $missing,
            'dry_run' => $context->dryRun,
        ]);
    }

    /**
     * @return array{0: array<int, string>, 1: array<int, string>}
     */
    private function parse(string $text): array
    {
        $wanted = [];
        $errors = [];
        foreach (preg_split('/\R/', $text) as $n => $line) {
            $line = trim((string) preg_replace('/#.*$/', '', $line));
            if ($line === '') {
                continue;
            }
            if (! preg_match('/^(\d+)\s*[=,\t ]\s*(\d{1,12})?$/', $line, $m)) {
                $errors[] = 'line '.($n + 1).': "'.mb_substr($line, 0, 40).'"';

                continue;
            }
            $id = (int) $m[1];
            $sid = $m[2] ?? '';
            if (isset($wanted[$id]) && $wanted[$id] !== $sid) {
                $errors[] = 'line '.($n + 1).": channel #{$id} is listed twice with different IDs";

                continue;
            }
            $wanted[$id] = $sid;
        }

        return [$wanted, $errors];
    }
}
