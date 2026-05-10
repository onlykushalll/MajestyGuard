using System;
using System.Text.Json;
using MajestyGuard.Core.Security;

namespace MajestyGuard.DpapiHelper
{
    internal static class Program
    {
        static int Main(string[] args)
        {
            if (args.Length < 1)
            {
                Console.Error.WriteLine("Usage: MajestyGuard.DpapiHelper.exe <embedding-store-path>");
                return 1;
            }

            var storePath = args[0];

            try
            {
                var store = new EmbeddingStore(storePath);
                var record = store.Load();

                if (record == null)
                {
                    Console.Error.WriteLine("No enrollment found");
                    return 2;
                }

                var vectors = new float[record.Embeddings.Length][];
                for (int i = 0; i < record.Embeddings.Length; i++)
                    vectors[i] = record.Embeddings[i].Vector;

                Console.Write(JsonSerializer.Serialize(vectors));
                return 0;
            }
            catch (UnauthorizedAccessException ex)
            {
                Console.Error.WriteLine($"SID mismatch: {ex.Message}");
                return 3;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Error: {ex.Message}");
                return 4;
            }
        }
    }
}
